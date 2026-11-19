"""Thin Gemini wrapper: send PDFs/text + a prompt, get Markdown back. Retries on rate limits/overload.

Two backends, chosen by GEMINI_BACKEND:
  api  (default) Gemini API with GEMINI_API_KEY from pipeline_academic/.env or my_ai_skills/.env
       (never printed). Free tier ≈ 20 requests/day. Model: GEMINI_MODEL (+ GEMINI_FALLBACK_MODELS).
       List what the key can use: `python scripts/gemini.py`. Usage: https://aistudio.google.com/usage
  agy  Antigravity CLI (agy.exe) logged in with the Google AI Pro account: subscription quota
       (≈1,500 requests/day). Model: AGY_MODEL (`agy models` lists them). Each call runs
       `agy -p` in a temp folder holding copies of the PDFs and context text.
"""
from __future__ import annotations

import json
import mimetypes
import os
import random
import shutil
import subprocess
import tempfile
import threading
import re
import time
from pathlib import Path

import common  # noqa: F401  (loads .env files)

mimetypes.add_type("image/webp", ".webp")       # missing from some Windows registries

DEFAULT_MODEL = "gemini-3.8-flash"
INLINE_LIMIT = 18 * 1024 * 1024        # total inline bytes per request; bigger PDFs go via the Files API
BACKEND = os.environ.get("GEMINI_BACKEND", "api").lower()
AGY_MODEL = os.environ.get("AGY_MODEL", "gemini-3.8-flash-medium")
AGY_TIMEOUT_S = int(os.environ.get("AGY_TIMEOUT_S", "1200"))
_client = None


class DailyQuotaExhausted(RuntimeError):
    """The model's per-day request quota is used up. Re-run after the reset; finished work is cached."""

    def __init__(self, retry_s: int | None):
        self.retry_s = retry_s
        when = ""
        if retry_s:
            from datetime import datetime, timedelta
            when = f"; resets in ~{retry_s / 3600:.1f} h (~{datetime.now() + timedelta(seconds=retry_s):%a %H:%M})"
        super().__init__(f"daily Gemini quota exhausted for {', '.join(models())}{when}")


def model_name() -> str:
    if BACKEND == "agy":
        return f"agy:{AGY_MODEL}"
    return os.environ.get("GEMINI_MODEL", DEFAULT_MODEL)


def client():
    global _client
    if _client is None:
        from google import genai
        key = os.environ.get("GEMINI_API_KEY")
        if not key:
            raise SystemExit("GEMINI_API_KEY not set (pipeline_academic/.env or my_ai_skills/.env)")
        _client = genai.Client(api_key=key)
    return _client


def pdf_parts(pdfs: list[Path]) -> tuple[list, list]:
    """PDFs → request parts: inline while the total stays under INLINE_LIMIT, else Files API uploads.
    Returns (parts, uploaded); pass `uploaded` to delete_uploads() when done."""
    from google.genai import types
    c = client()
    parts, uploaded, inline = [], [], 0
    for pdf in pdfs:
        size = pdf.stat().st_size
        mime = mimetypes.guess_type(pdf.name)[0] or "application/pdf"     # PDFs, and screenshots (ingest_intel)
        if inline + size <= INLINE_LIMIT:
            parts.append(types.Part.from_bytes(data=pdf.read_bytes(), mime_type=mime))
            inline += size
        else:
            f = c.files.upload(file=str(pdf), config={"mime_type": mime})
            while f.state and f.state.name == "PROCESSING":
                time.sleep(3)
                f = c.files.get(name=f.name)
            uploaded.append(f)
            parts.append(f)
    return parts, uploaded


def delete_uploads(uploaded: list) -> None:
    for f in uploaded:
        try:
            client().files.delete(name=f.name)
        except Exception:
            pass


def models() -> list[str]:
    """Primary model, then GEMINI_FALLBACK_MODELS (comma-separated). A fallback is used when the
    model is overloaded (503) or out of daily quota (the free-tier cap is per model)."""
    if BACKEND == "agy":
        return [f"agy:{AGY_MODEL}"]
    extra = os.environ.get("GEMINI_FALLBACK_MODELS", "gemini-3.7-flash,gemini-3.6-flash,gemini-3.5-flash")
    return [model_name()] + [m.strip() for m in extra.split(",") if m.strip() and m.strip() != model_name()]


_tl = threading.local()
_capped: set[str] = set()      # models that hit their daily cap in this process


def used_model() -> str:
    """The model that produced this thread's last successful call (for frontmatter)."""
    return getattr(_tl, "model", models()[0])


def call(parts: list, temperature: float = 0.3, search: bool = False, retries: int = 2) -> str:
    """One generate_content call, falling back through models(). `search` enables Google Search
    grounding (textbooks, other universities' exams on the same topics)."""
    if BACKEND == "agy":               # text-only calls (e.g. the solution check); PDFs go via generate()
        if not all(isinstance(p, str) for p in parts):
            raise TypeError("agy backend: pass PDFs to generate(), not pre-built parts")
        return _agy([], list(parts[:-1]), parts[-1], search)
    from google.genai import types
    config = types.GenerateContentConfig(
        temperature=temperature,
        tools=[types.Tool(google_search=types.GoogleSearch())] if search else None,
    )
    last_delay, last_err = None, None
    for model in models():
        if model in _capped:
            continue
        for attempt in range(1, retries + 1):
            try:
                resp = client().models.generate_content(model=model, contents=parts, config=config)
                _tl.model = model
                return strip_fences(resp.text or "")
            except Exception as e:  # SDK raises typed errors; match on status text to stay version-proof
                msg, last_err = str(e), e
                if "PerDay" in msg:        # daily cap for this model: retrying won't help, try the next
                    delay = re.search(r"retryDelay'?:\s*'?(\d+)s", msg)
                    last_delay = int(delay.group(1)) if delay else last_delay
                    _capped.add(model)
                    break
                if re.search(r"429|503|500|RESOURCE_EXHAUSTED|UNAVAILABLE|overloaded", msg):
                    if attempt < retries:
                        time.sleep(min(40, 2 ** attempt * 3) + random.random() * 3)
                        continue
                    break                  # still overloaded: try the next model
                raise
    if all(m in _capped for m in models()):
        raise DailyQuotaExhausted(last_delay)
    raise last_err or RuntimeError("no Gemini model available")


AGY_RULES = """
---
How to run this task:
- The input files are in the working directory {tmp}: {names}.
  Read every one of them completely (every page of every PDF) before answering.
- Do not create, edit or delete files, do not run commands, do not write a plan and do not ask for
  approval or clarification. {search}
- Do not use any tool except reading these input files. Do every calculation yourself in your
  head. (In this non-interactive mode any other tool call ends the run with no answer.)
- Your final answer must be only the requested Markdown output, nothing before or after it.
"""
AGY_RETRY = ("\n\nIMPORTANT: a previous attempt ended with no answer because it called a tool. Read the "
             "input files, then reply directly with the full answer. Call no other tool.")


_ARTIFACT_LINK = re.compile(r"\(file:///([^)\s]+?\.md)\)")


def salvage_artifacts(response: str) -> str:
    """agy sometimes acts as an agent: it writes the answer to a Markdown file (in its brain folder or the temp
    workspace) and replies with only a summary and a link (seen 2026-11-17). If the reply links such files and
    they hold more than the reply itself, return their content instead, so the answer isn't lost."""
    from urllib.parse import unquote
    texts = []
    for m in _ARTIFACT_LINK.finditer(response):
        p = Path(unquote(m.group(1)))
        if p.is_file() and p.suffix == ".md" and p.stat().st_size > 1.5 * len(response):
            body = p.read_text("utf-8", errors="replace")
            if body not in texts:
                texts.append(body)
    return "\n\n".join(texts) if texts else response


def agy_exe() -> str:
    return shutil.which("agy") or os.path.expandvars(r"%LOCALAPPDATA%\agy\bin\agy.exe")


def context_name(text: str, taken: set[str]) -> str:
    """A file name that says what a text block is, so citations in the output mean something to a reader:
    'HANDWRITTEN (...): handwritten_ABCD1001_Lecture.md' → handwritten_ABCD1001_Lecture.md,
    'UNIT OUTLINE (official...' → unit_outline.md, 'WEAK POINTS (...' → weak_points.md."""
    t = text.lstrip()
    m = re.match(r"HANDWRITTEN[^\n:]*:\s*(.+?)(?:\.md)?\s*$", t.split("\n", 1)[0])
    if m:
        stem = re.sub(r"[^\w.-]+", "_", m.group(1)).strip("_")     # "handwritten_Scan from ... PM" → handwritten_Scan_from_..._PM
    else:
        m = re.match(r"[A-Z][A-Z' ]{1,40}?(?=\s*[(:\n]|\s+[a-z]|$)", t)
        stem = re.sub(r"\W+", "_", m.group(0).replace("'", "").strip().lower()).strip("_") if m else ""
    stem = stem or "context"
    name, k = f"{stem}.md", 2
    while name in taken:
        name, k = f"{stem}_{k}.md", k + 1
    taken.add(name)
    return name


def _agy(pdfs: list[Path], texts: list[str], prompt: str, search: bool = False) -> str:
    """One `agy -p` run. Inputs are copied into a temp workspace so the agent can't touch originals."""
    with tempfile.TemporaryDirectory(prefix="agy_") as tmp:
        names = []
        for i, p in enumerate(pdfs):
            dst = Path(tmp) / f"{i:02d}_{re.sub(r'[^\w.-]+', '_', p.name)}"
            shutil.copyfile(p, dst)
            names.append(dst.name)
        taken = {"TASK.md"} | set(names)
        for t in texts:
            dst = Path(tmp) / context_name(t, taken)
            dst.write_text(t, "utf-8")
            names.append(dst.name)
        rules = AGY_RULES.format(tmp=tmp, names=", ".join(names) or "(none)",
                                 search="You may search the web." if search else "Do not search the web.")
        for attempt in range(2):        # 2nd attempt only if the 1st ended empty (tool call in print mode)
            task = prompt + "\n" + rules + (AGY_RETRY if attempt else "")
            if len(task) > 20000:       # Windows command lines cap at ~32k chars: pass long tasks as a file
                (Path(tmp) / "TASK.md").write_text(task, "utf-8")
                task = (f"Your task is in the file TASK.md in {tmp}. Read it and follow it exactly. Call no tool "
                        "other than reading files. Your final answer must be only the Markdown output it asks for.")
            cmd = [agy_exe(), "-p", task, "--model", AGY_MODEL, "--add-dir", tmp, "--output-format", "json",
                   "--print-timeout", f"{AGY_TIMEOUT_S}s"]
            try:
                r = subprocess.run(cmd, cwd=tmp, stdin=subprocess.DEVNULL, capture_output=True, text=True,
                                   encoding="utf-8", errors="replace", timeout=AGY_TIMEOUT_S + 60)
            except subprocess.TimeoutExpired:
                raise RuntimeError(f"agy timed out after {AGY_TIMEOUT_S}s") from None
            out = r.stdout.strip()
            try:
                data = json.loads(out[out.index("{"):]) if "{" in out else {}
            except ValueError:
                data = {}
            if data.get("status") == "SUCCESS" and data.get("response", "").strip():
                _tl.model = f"agy:{AGY_MODEL}"
                return strip_fences(salvage_artifacts(data["response"]))
            if data.get("status") != "SUCCESS":
                break                   # a real error: don't retry, report it
    msg = (out + " " + r.stderr)[-600:]
    if re.search(r"quota|RESOURCE_EXHAUSTED|rate limit|429", msg, re.I):
        raise DailyQuotaExhausted(None)
    raise RuntimeError(f"agy failed (exit {r.returncode}): {msg.strip()[:300]}")


def generate(pdfs: list[Path], prompt: str, texts: list[str] | None = None, temperature: float = 0.3,
             search: bool = False, retries: int = 5) -> str:
    """PDFs + text blocks + prompt in one call; uploads are cleaned up afterwards."""
    if BACKEND == "agy":
        return _agy(pdfs, list(texts or []), prompt, search)
    parts, uploaded = pdf_parts(pdfs)
    try:
        return call(parts + list(texts or []) + [prompt], temperature, search, retries)
    finally:
        delete_uploads(uploaded)


def pdf_to_markdown(pdf: Path, prompt: str, temperature: float = 0.3, retries: int = 5) -> str:
    return generate([pdf], prompt, temperature=temperature, retries=retries)


def strip_fences(s: str) -> str:
    """Every AI response passes through here: unwrap a Markdown fence and remove emojis (project rule)."""
    s = common.no_emoji(s.strip())
    m = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```$", s, re.S)
    return m.group(1).strip() if m else s


if __name__ == "__main__":       # list the Gemini models this key can call (free: no quota used)
    for m in sorted(m.name for m in client().models.list()):
        if "gemini" in m:
            print(m.removeprefix("models/"))
