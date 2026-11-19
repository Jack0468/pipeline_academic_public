"""Peer-safe export of the vault: the study material classmates can use, none of your personal data.

    python scripts/share_vault.py                     # all units → SHARE_PATH (default: OneDrive\\SHARED\\usyd-course-notes)
    python scripts/share_vault.py --unit ABCD1001 --include-handbook   # only once the formula-sheet author agrees
    python scripts/share_vault.py --dry-run           # list what would be exported (and what is held back)
    python scripts/share_vault.py --allow-assignment-derived   # after reviewing the held-back mocks
Usage guide, review checklist and how to give classmates access: docs/SHARING.md

Layout (shaped for a future students-for-students repo, docs/ideas/idea1.md):
    README.md                                  what this is, how to use it, integrity note, provenance
    <CODE>/<YEAR>-S<n>/README.md               unit, session, coordinator (from the public outline), contents
    <CODE>/<YEAR>-S<n>/outline.md              official outline summary (public anyway)
    <CODE>/<YEAR>-S<n>/recall/<CODE>_L<n>.md   recall prompts, exam-style problems with answers, time allocations
    <CODE>/<YEAR>-S<n>/mock_exams/             mock exams + solutions
    <CODE>/<YEAR>-S<n>/handbook.md             only with --include-handbook
    how_to_study.md

Included: recall files, mock exams, unit outlines, the study method (and the handbook if asked).
Never included: marks, Canvas data, error logs, recall/practice checks, progress, the study plan,
handwriting transcriptions, own work, slides, other people's sheets.
Sanitising: file:// links and personal frontmatter removed; vault links → plain text (or relative
links between exported recall files); "Trap (you)" → "Trap (common error)"; no emojis.
Mock exams additionally (clean_mock): `*Targets:*` lines (your weak points) and `*Values:*` lines (random
draws) removed; source notes keep lecture pages, textbooks and practical briefs but drop your own material
(marker notes, handwritten/student notes, answer sheets, submissions) and assignment references; the
coordinator's warnings and pipeline-bug corrections removed. A mock is exported only with its solutions, and
a mock with questions that mirror an assignment is held back until you review it (--allow-assignment-derived).
A final scan refuses to write anything if a file still contains your SID, name, email, an absolute local
path, a SHARE_BLOCK_PATTERNS match (.env), or (mocks) a trace of your record.
Each run syncs the export: new and changed files are written first, then files no longer in the export are
removed (so a held-back mock also disappears from an earlier export). A failure part-way never empties the
share, read-only folders (OneDrive sets the attribute) are handled, and anything that can't be removed is a
warning. --unit X updates only that unit's folder (plus the top-level README); other units are left alone.

Peer submissions (peer.py, docs/REFACTOR_PLAN.md "Peer share"): each exported mock also gets a results template
with stable question IDs, `peer.py` goes to the top of the share, and classmates' files in `_submissions/` become
class results (aggregates only, at least peer.MIN_N attempts). The export never writes, rewrites or deletes anything
in `_submissions/`; rejected submissions and new error reports are listed in the output.
Where the share is: SHARE_PATH, else the folder peer.find_share() finds on this machine, else
SHARED/usyd-course-notes next to the coursework folder (created on the first export).
"""
from __future__ import annotations

import argparse
import json
import os
import re
import stat
import sys
import time
from datetime import datetime
from pathlib import Path

import peer
from common import (CONFIG, ONEDRIVE_COURSEWORK, STATE, TZ, VAULT, exam_over, load_units, load_yaml, no_emoji,
                    unit_path)

MARKER = peer.MARKER
PEER_SOURCE = Path(__file__).with_name("peer.py")
GENERIC_PERSONAL = [r"\b5\d{8}\b", r"[\w.+-]+@gmail\.com", r"(?i)file:///", r"(?i)\b[a-z]:\\users\\",
                    r"(?i)onedrive - the university"]          # SIDs, Gmail addresses, local links and user folders


def own_patterns(key: str = "personal_patterns") -> list[str]:
    """Patterns that name you (your name, account names): `config/share.yaml`, which stays private and is never
    published with the app."""
    p = CONFIG / "share.yaml"
    return [str(x) for x in (load_yaml(p).get(key) or [])] if p.exists() else []


# Extra regexes that must never be shared (classmates' names, other IDs): SHARE_BLOCK_PATTERNS in .env, comma-separated
PERSONAL = GENERIC_PERSONAL + own_patterns() + [p.strip() for p in os.environ.get("SHARE_BLOCK_PATTERNS", "").split(",")
                                                if p.strip()]
KEEP_FRONT = {"unit", "lecture", "source", "rank", "model", "generated", "type", "mock", "research", "mock_uid"}

# Mock exams are written from the sharer's record (error log, marker feedback, own submissions, handwritten notes,
# weak points). clean_mock() removes those traces; MOCK_GUARD then refuses the export if any survive.
PRIVATE_SOURCE = re.compile(r"(?i)marker|student|handwritten|context_\d+|own work|answer.?sheet|submission|error.?log|"
                            r"weak|practi[cs]ed|recall|record|canvas|\.md\b|my notes|your notes|intel|word on the street")
ASSIGNMENT = re.compile(r"(?i)\bassignment\b|\bassgn?\b|\bA[1-9]\s*Q\d")
MOCK_GUARD = [r"(?im)^\*Targets:", r"(?i)student record", r"(?i)marker (notes|comment|feedback)", r"(?i)\berror.?log\b",
              r"(?i)student notes", r"(?i)\bcontext_\d+", r"(?i)weak points?", r"(?i)\bassignment \d"]
SOURCE_LINE = re.compile(r"(?m)^\*Source:\*?\s*(.*?)\s*\*?[ \t]*$")
QUESTION = re.compile(r"^## Question (\d+)[^\n]*\n.*?(?=^## Question |^# |\Z)", re.S | re.M)


def share_root() -> Path:
    """Where the export goes (see the module docstring)."""
    if os.environ.get("SHARE_PATH"):
        return Path(os.environ["SHARE_PATH"])
    return peer.find_share() or ONEDRIVE_COURSEWORK.parent / "SHARED" / peer.SHARE_NAME


def with_front(md: str, key: str, value: str) -> str:
    """md with `key: value` added at the top of its front matter (or in a new one)."""
    if re.match(r"---\r?\n", md):
        return re.sub(r"\A---\r?\n", f"---\n{key}: {value}\n", md, count=1)
    return f"---\n{key}: {value}\n---\n" + md


def mock_results(code: str, session: str, n: int, q_md: str, s_md: str, inbox: Path,
                 today: str) -> tuple[str, dict[str, str], list[str]]:
    """The results template for one exported mock, plus its class results once anything was sent back.
    -> (mock_uid or '', {file name: text}, messages)"""
    rows, warnings = peer.template_rows(code, session, n, q_md, s_md)
    notes = [f"{code} mock {n}: {w}" for w in warnings]
    if not rows:
        return "", {}, notes + [f"{code} mock {n}: no question marks found, so no results template"]
    files = {f"mock_exam_{n}_results_template.csv": peer.to_csv(rows)}
    folder = inbox / code / session
    subs, rejected = peer.collect(folder / f"mock_exam_{n}", rows)
    notes += [f"{code} mock {n}: REJECTED submission {r}" for r in rejected]
    errata = peer.errata_counts(folder / "errata", n)
    if subs or errata:
        agg = peer.aggregate(rows, subs, errata)
        files[f"mock_exam_{n}_class_results.md"] = peer.class_results_md(agg, today)
        files[f"mock_exam_{n}_class_results.json"] = json.dumps(agg, indent=1) + "\n"
        notes.append(f"{code} mock {n}: {len(subs)} submission(s), {sum(errata.values())} error report(s)")
    return rows[0]["question_id"].split("-Q")[0], files, notes


def readme_example(units: list[dict], exported: list[tuple[dict, int]]) -> tuple[str, int]:
    """(unit code, mock number) for the README's example commands: an exported mock of the next unit whose exam
    is still ahead, else any exported mock, else a placeholder."""
    ahead = [(u, n) for u, n in exported if not exam_over(u)]
    if ahead or exported:
        u, n = (ahead or exported)[0]
        return u["code"], n
    return (units[0]["code"] if units else "ABCD1234"), 1


def errata_review(inbox: Path) -> list[str]:
    """Classmates' error reports, for you to check. Written to state/share/errata.md, never published."""
    lines = []
    for f in sorted(inbox.glob("*/*/errata/*.md")):
        try:
            text = f.read_text("utf-8", errors="replace")
        except OSError:
            continue
        fm = peer.front_matter(text)
        body = " ".join(re.sub(r"\A---\n.*?\n---\n", "", text.replace("\r\n", "\n"), flags=re.S).split())
        lines.append(f"- {fm.get('date', '?')} {fm.get('unit', '?')} mock {fm.get('mock', '?')} "
                     f"{fm.get('question', '?')}: {body} ({f.relative_to(inbox)})")
    if lines:
        out = STATE / "share" / "errata.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# Error reports from classmates\n\nCheck each one against the solution; fix the vault "
                       "copy by hand if it is right.\n\n" + "\n".join(lines) + "\n", "utf-8")
    return lines


def mock_papers(d: Path) -> list[Path]:
    """mock_exam_<n>.md papers in numeric order (not solutions, not attempts)."""
    no = lambda p: int(re.fullmatch(r"mock_exam_(\d+)", p.stem).group(1))
    return sorted((p for p in d.glob("mock_exam_*.md") if re.fullmatch(r"mock_exam_\d+", p.stem)), key=no)


def assignment_questions(solutions: str) -> list[int]:
    """Questions whose source note says they mirror or adapt an assignment question."""
    out = []
    for m in QUESTION.finditer(solutions):
        src = SOURCE_LINE.search(m.group(0))
        if src and ASSIGNMENT.search(src.group(1)):
            out.append(int(m.group(1)))
    return out


def clean_source(text: str) -> str:
    """Keep the public parts of a source note (lecture pages, textbooks, practical briefs); drop the sharer's own
    material and assignment references."""
    text = re.sub(r"\s*\(([^()]*)\)", lambda m: "" if PRIVATE_SOURCE.search(m.group(1)) or ASSIGNMENT.search(m.group(1))
                  else m.group(0), text)
    parts = [p.strip() for p in re.split(r"[;,](?![^()]*\))", text) if p.strip()]
    keep = [p for p in parts if not PRIVATE_SOURCE.search(p) and not ASSIGNMENT.search(p)]
    return ", ".join(keep) or "lecture slides"


def clean_mock(md: str) -> str:
    """A mock paper or its solutions, minus the traces of the sharer's own record. Run before clean()."""
    md = re.sub(r"(?m)^\*Targets:.*(?:\n|\Z)", "", md)                  # which of *my* weak points it targets
    md = re.sub(r"(?m)^\*Values:.*(?:\n|\Z)", "", md)                   # the random draws (meaningless to others)
    md = SOURCE_LINE.sub(lambda m: f"*Source: {clean_source(m.group(1))}*", md)
    md = re.sub(r"(?ms)^> \[!warning\] Not fully clean.*?(?=^(?!>)|\Z)", "", md)   # coordinator notes
    md = re.sub(r"(?m)^> \[!trap\] Corrected: The original solution was for an unrelated question.*(?:\n|\Z)", "", md)
    md = re.sub(r"(?m)^> \[!tip\] Sit this timed.*$",
                "> [!tip] Sit this timed, with only the allowed materials, then mark it with the solutions file.", md)
    return md


def session_dir(u) -> str:
    m = re.match(r"(\d{4})_s[c]?(\d)", str(u.get("session", "")).lower())
    return f"{m.group(1)}-S{m.group(2)}" if m else str(u.get("session", "session"))


def clean(md: str, recall_names: set[str]) -> str:
    """Strip personal links/metadata and vault-only syntax."""
    if md.startswith("---"):
        _, fm, body = md.split("---", 2)
        keep = [l for l in fm.strip().splitlines() if l.split(":", 1)[0].strip() in KEEP_FRONT]
        md = "---\n" + "\n".join(keep) + "\n---" + body
    md = re.sub(r"\[([^\]]*)\]\(file:///[^)]*\)", r"\1", md)                  # [text](file:///...) → text
    md = re.sub(r"\s*·?\s*\[Open slides\]", "", md)

    def wl(m):
        target, label = m.group(1), m.group(2) or m.group(1)
        name = target.split("#")[0].split("/")[-1]
        if name in recall_names:                                             # keep links between recall files
            return f"[[{name}|{label}]]"
        return label.split("/")[-1]
    md = re.sub(r"\[\[([^\]|]+)(?:\|([^\]]*))?\]\]", wl, md)
    md = md.replace("**Trap (you):**", "**Trap (common error):**").replace("Trap (you)", "Trap (common error)")
    md = re.sub(r"(?im)^> \[!check\] AI-generated from the slides\..*$",
                "> [!check] AI-generated from the lecture slides. Answer before expanding; if an answer disagrees "
                "with the slides, the slides win.", md)
    return no_emoji(md)


def personal_hits(text: str) -> list[str]:
    return [p for p in PERSONAL if re.search(p, text)]


def outline_md(u) -> str:
    p = unit_path(u, "outline")
    if not p.exists():
        return ""
    t = p.read_text("utf-8")
    t = re.sub(r"> \[!important\] Final exam:.*?(?=\n\n|\Z)", "", t, flags=re.S)  # has your seat/venue
    return t


def readme_unit(u, files: dict[str, list[str]], outline: str) -> str:
    staff = re.search(r"\*\*Teaching staff:\*\* (.*)", outline)
    staff = re.sub(r"[\w.+-]+@[\w.-]+", "", staff.group(1)).strip(" ,;") if staff else "see outline"
    L = [f"# {u['code']} {u['name']} ({session_dir(u)})", "",
         f"Coordinator / staff (from the public unit outline): {staff}", "",
         "| Folder | What | Files |", "|---|---|---|",
         f"| `recall/` | per lecture: free-recall prompts, exam-style problems (marks and time allocations), "
         f"answers (collapsed), notes-sheet candidates, common traps | {len(files.get('recall', []))} |",
         f"| `mock_exams/` | full mock exams in the real format, with solutions, a results template and (once "
         f"classmates send marks back) class results | {len(files.get('mock_exams', []))} |",
         f"| `outline.md` | official outline: learning outcomes, assessment, weekly schedule | 1 |"]
    if files.get("handbook"):
        L.append("| `handbook.md` | exam lookup handbook: formulas with units and conditions, traps, concepts | 1 |")
    return "\n".join(L) + "\n"


TOP_README = """# USyd course study notes (peer share)

Study material for these units, shared between classmates. Open the folder as an Obsidian vault, or read the
Markdown directly (any Markdown viewer with LaTeX maths works).

{units}

## How to use it
1. Per lecture, answer the **free-recall prompts** closed-book first, then check against the slides.
2. Do the **exam-style problems** against the time allocation shown (exam pace per unit). Answers are collapsed:
   attempt first.
3. Sit a **mock exam** under real conditions before the final.
See `how_to_study.md` for the full method.

## Send back your marks (optional, about 5 minutes)
After sitting a mock, record your marks per question part, so everyone can see which questions the class finds
hard. From this folder (Python 3.10 or newer, nothing to install):

    python peer.py record {example}             your marks for {example_words}, part by part
    python peer.py results {example}            the class results next to yours
    python peer.py report {example} Q3 "..."    an error in a question or its solution
    python peer.py where                         where it found this folder, and your token

No Python? Fill in the mock's `mock_exam_<n>_results_template.csv` and save it as
`_submissions/<unit>/<session>/mock_exam_<n>/<any name>.csv`.

**What is shared, and who sees it.** Your file holds your marks per part, the option you chose (multiple choice),
minutes if you give them, the month you sat it, how it was marked and a random token made on your machine. No name,
SID or email. Only class aggregates are published, and only once at least 5 people have attempted a question.
OneDrive does show who created each file in `_submissions/`, to the sharer and to everyone in this group, so it is
not anonymous. Practice mocks only: never real exam questions, answers or marks. To withdraw, delete your files in
`_submissions/`, or send the sharer your token (`python peer.py token`).

## Read this
- **Most content is AI-generated** from the lecture slides (each file says so). Answers were spot-checked, not
  all verified: the slides and your lecturer are the authority. If you find an error, report it (above).
- **Academic integrity:** this is revision material, not assessment answers. It contains no assignment
  solutions. Don't use it for anything that is assessed.
- **No personal data:** marks, feedback and personal notes are deliberately not included.

_Generated {date} by pipeline_academic `share_vault.py`. Layout: `<unit>/<year>-<session>/` so future
semesters and lecturers can sit side by side._
"""


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--include-handbook", action="store_true",
                    help="also export handbook.md (only once the author of any formula sheet it builds on agrees)")
    ap.add_argument("--allow-assignment-derived", action="store_true",
                    help="also export mocks with questions that mirror an assignment, after you have checked that "
                         "none gives away an assignment answer (the assignment references are removed)")
    ap.add_argument("--inbox", help="classmates' submissions folder, if not _submissions/ in the share "
                                    "(also SHARE_INBOX)")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args()
    units = load_units()                     # all units feed the top README; --unit limits what is written
    chosen = {u["code"] for u in load_units(a.unit)}
    if a.unit and not chosen:
        sys.exit(f"no configured unit matches {a.unit}")
    share = share_root()
    inbox = peer.inbox_root(share, a.inbox)
    today = f"{datetime.now(TZ):%Y-%m-%d}"
    if share.exists() and any(share.iterdir()) and not (share / MARKER).exists():
        sys.exit(f"{share} exists and wasn't made by this script; set SHARE_PATH to an empty or exported folder")
    plan, blocked, held, notes = [], [], [], []                     # (dest, text), (dest, hits), (unit, file, why)
    exported: list[tuple[dict, int]] = []                           # (unit, mock number) of every exported mock
    unit_rows = []
    for u in units:
        base = Path(u["code"]) / session_dir(u)
        if u["code"] not in chosen:          # not exported this run: its folder in the share is left as it is
            unit_rows.append(f"- **[{u['code']} {u['name']}]({u['code']}/{session_dir(u)}/README.md)**")
            continue
        recall = sorted(unit_path(u, "recall").glob("*.md"))
        names = {p.stem for p in recall}
        files: dict[str, list[str]] = {"recall": [], "mock_exams": []}
        for p in recall:
            plan.append((base / "recall" / p.name, clean(p.read_text("utf-8"), names)))
            files["recall"].append(p.name)
        for q in mock_papers(unit_path(u, "practice")):
            s = q.with_name(f"{q.stem}_solutions.md")
            if not s.exists():
                held.append((u["code"], q.name, "no solutions file yet"))
                continue
            q_md, s_md = q.read_text("utf-8"), s.read_text("utf-8")
            derived = assignment_questions(s_md)
            if derived and not a.allow_assignment_derived:
                held.append((u["code"], q.name, "questions " + ", ".join(f"Q{k}" for k in derived) + " mirror an "
                             "assignment question: review them (docs/SHARING.md), then re-run with "
                             "--allow-assignment-derived"))
                continue
            n = int(re.fullmatch(r"mock_exam_(\d+)", q.stem).group(1))
            uid, extras, msgs = mock_results(u["code"], session_dir(u), n, q_md, s_md, inbox, today)
            notes += msgs
            for p, raw in ((q, q_md), (s, s_md)):
                text = clean(clean_mock(with_front(raw, "mock_uid", uid) if uid else raw), names)
                plan.append((base / "mock_exams" / p.name, text))
                guard = [g for g in MOCK_GUARD if re.search(g, text)]
                if guard:
                    blocked.append((base / "mock_exams" / p.name, guard))
            plan += [(base / "mock_exams" / name, text) for name, text in extras.items()]
            files["mock_exams"].append(q.name)
            exported.append((u, n))
        outline = outline_md(u)
        if outline:
            plan.append((base / "outline.md", clean(outline, names)))
        handbook = unit_path(u, "handbook")
        if a.include_handbook and handbook.exists():
            plan.append((base / "handbook.md", clean(handbook.read_text("utf-8"), names)))
            files["handbook"] = ["handbook.md"]
        plan.append((base / "README.md", readme_unit(u, files, outline)))
        unit_rows.append(f"- **[{u['code']} {u['name']}]({u['code']}/{session_dir(u)}/README.md)**: "
                         f"{len(files['recall'])} lectures, {len(files['mock_exams'])} mock exam(s) with solutions")
    how = VAULT / "00_dashboard" / "how_to_study.md"
    if how.exists():
        plan.append((Path("how_to_study.md"), clean(how.read_text("utf-8"), set())))
    code, n = readme_example(units, exported)
    plan.append((Path("README.md"), TOP_README.format(units="\n".join(unit_rows), date=today, example=f"{code} {n}",
                                                      example_words=f"{code} mock exam {n}")))
    plan.append((Path("peer.py"), PEER_SOURCE.read_text("utf-8")))

    for dest, text in plan:
        hits = personal_hits(text)
        if hits:
            blocked.append((dest, hits))
    for code, name, why in held:
        print(f"  HELD BACK {code} {name}: {why}")
    for note in notes:
        print(f"  {note}")
    reports = errata_review(inbox)
    if reports:
        print(f"  {len(reports)} error report(s) from classmates to check: {STATE / 'share' / 'errata.md'}")
    if blocked:
        for dest, hits in blocked:
            print(f"  BLOCKED {dest}: still contains {hits}")
        sys.exit(f"{len(blocked)} file(s) failed the personal-data scan; nothing written")
    print(f"  {len(plan)} file(s) pass the personal-data scan → {share}")

    def in_scope(rel: Path) -> bool:                                 # top-level files + chosen units
        return len(rel.parts) == 1 or rel.parts[0] in chosen
    stale = stale_files(share, {dest for dest, _ in plan}, in_scope)
    if a.dry_run:
        for dest, _ in plan:
            print(f"    {dest}")
        for p in stale:
            print(f"    (remove) {p.relative_to(share)}")
        return
    sync(share, inbox, plan, stale, in_scope)


def stale_files(share: Path, keep: set[Path], in_scope) -> list[Path]:
    """Files in the share that are no longer part of the export. Never the marker, never anything in _submissions/
    (classmates' files)."""
    if not share.exists():
        return []
    out = []
    for p in share.rglob("*"):
        rel = p.relative_to(share)
        if p.is_file() and p.name != MARKER and rel.parts[0] != peer.INBOX and in_scope(rel) and rel not in keep:
            out.append(p)
    return sorted(out)


def _writable(path: Path) -> None:
    """Clear the Windows read-only attribute (OneDrive sets it on folders, which makes rmdir fail)."""
    try:
        os.chmod(path, stat.S_IWRITE | stat.S_IREAD)
    except OSError:
        pass
    if os.name == "nt":
        import ctypes
        attrs = ctypes.windll.kernel32.GetFileAttributesW(str(path))
        if attrs != -1 and attrs & 0x1:                                  # FILE_ATTRIBUTE_READONLY
            ctypes.windll.kernel32.SetFileAttributesW(str(path), attrs & ~0x1)


def _remove(path: Path, tries: int = 4) -> str | None:
    """Delete a file or an empty folder; None on success, else why not. Retries: OneDrive briefly holds handles."""
    for i in range(tries):
        try:
            path.rmdir() if path.is_dir() else path.unlink()
            return None
        except FileNotFoundError:
            return None
        except OSError as e:
            _writable(path)
            if i == tries - 1:
                return str(e)
            time.sleep(0.5 * (i + 1))


def sync(share: Path, inbox: Path, plan: list, stale: list[Path], in_scope) -> None:
    """Write the export, then remove what is no longer in it. New content lands first, so a failure part-way
    never leaves the share emptier than before; files whose text is unchanged are not rewritten (less OneDrive
    churn). Anything that can't be removed is reported, not fatal. The submissions folder is created if missing
    and otherwise never touched."""
    share.mkdir(parents=True, exist_ok=True)
    (share / MARKER).write_text("created by pipeline_academic share_vault.py; the peer tools find the folder by this "
                                "file\n", "utf-8")
    try:
        inbox.mkdir(parents=True, exist_ok=True)
    except OSError as e:
        print(f"  WARN could not create the submissions folder {inbox}: {e}")
    written = same = 0
    failed = []
    for dest, text in plan:
        out = share / dest
        try:
            if out.exists() and out.read_text("utf-8") == text:
                same += 1
                continue
            out.parent.mkdir(parents=True, exist_ok=True)
            if out.exists():
                _writable(out)
            out.write_text(text, "utf-8")
            written += 1
        except OSError as e:
            failed.append(f"write {dest}: {e}")
    removed = 0
    for p in stale:
        why = _remove(p)
        removed += why is None
        if why:
            failed.append(f"remove {p.relative_to(share)}: {why}")
    dirs = sorted((p for p in share.rglob("*") if p.is_dir() and p.relative_to(share).parts[0] != peer.INBOX
                   and in_scope(p.relative_to(share))), key=lambda p: len(p.parts), reverse=True)   # deepest first
    for p in dirs:
        if not any(p.iterdir()):
            why = _remove(p)
            if why:
                failed.append(f"remove empty folder {p.relative_to(share)}: {why}")
    print(f"  {written} written, {same} unchanged, {removed} stale file(s) removed → {share}")
    for f in failed:
        print(f"  WARN {f}")
    if failed:
        print("  Close anything that has these open (Explorer, Obsidian, an editor), let OneDrive finish syncing, "
              "then re-run; nothing is lost in the meantime.")
    print(f"  Sharing: the folder 'Can view', and {peer.INBOX} 'Can edit', for the same classmates "
          "(docs/SHARING.md).")


if __name__ == "__main__":
    main()
