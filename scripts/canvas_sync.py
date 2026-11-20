"""Pull marks, marker feedback, announcements and announcement files from Canvas (read-only).

Needs CANVAS_TOKEN in .env (Canvas → Account → Settings → New access token); CANVAS_BASE defaults
to https://canvas.sydney.edu.au. The token is never printed. Each unit's course is found by
course code (e.g. "ABCD1001 (ND)"), or set `canvas_course_id:` in the unit config.

Per unit:
  - <vault>/units/<id>/canvas.md   marked work (score, %, marker comment), exam-related
                                   announcements in full, other announcements, downloaded files
  - practice/error_log.md          each marker comment appended once, as a lost-mark entry
                                   (append-only; your own entries are never touched)
  - state/canvas/<CODE>.json       everything, structured (used by gen_practice_exam.py)
  - state/canvas/<id>/files/       announcement attachments; lecture-like PDFs go in files/lectures/,
                                   which is part of the unit's lecture sources (→ recall material)
  - <unit OneDrive folder>/canvas/<module>/   mirror of the course's module files: File items, and files
                                   linked inside module Pages (units that post slides on weekly pages).
                                   THE GROUND TRUTH for lectures and tutorials (since 2026-11-20): every file
                                   is saved here, even if you have a copy elsewhere, and the unit config's
                                   lecture and tutorial globs point here. Images and videos are skipped
                                   (--videos adds videos). state/canvas/<id>/module_files.json remembers each
                                   file's Canvas version, so re-runs fetch only what changed. Nothing is
                                   deleted, and nothing is added to the pipeline's sources automatically:
                                   canvas.md lists saved files no `sources:` glob covers yet.
                                   (The course Files area itself returns 403 to students at USyd; modules don't.)
It also compares Canvas scores with the marks the unit config still *assumes*, so you know what
to update in results.xlsx (which scripts never write to).

Usage:
    python scripts/canvas_sync.py [--unit ABCD1004] [--no-files] [--videos]
"""
from __future__ import annotations

import argparse
import html
import json
import os
import re
import sys
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime
from pathlib import Path

from common import ALLOW_SAT_HELP, work_units
from common import STATE, TZ, find_sources, load_units, unit_dir, unit_path

BASE = os.environ.get("CANVAS_BASE", "https://canvas.sydney.edu.au").rstrip("/")
EXAMY = re.compile(r"\bexam|final|revision|review|scope|formula sheet|cheat sheet|study vac", re.I)
LECTURY = re.compile(r"lecture|slides?|week|notes|tutorial|revision", re.I)
CANVAS_DIR = "canvas"                   # module files mirror: <unit OneDrive folder>/canvas/<module>/
SKIP_CLASSES = {"image", "video", "audio"}   # icons and staff photos; videos only with --videos


def token() -> str:
    t = os.environ.get("CANVAS_TOKEN", "").strip()
    if not t:
        sys.exit("CANVAS_TOKEN missing in .env (Canvas → Account → Settings → New access token)")
    return t


def request(url: str):
    req = urllib.request.Request(url, headers={"Authorization": f"Bearer {token()}",
                                               "User-Agent": "pipeline_academic"})
    return urllib.request.urlopen(req, timeout=60)


def get(path: str, **params) -> list | dict:
    """GET with pagination (follows the Link: rel=next header)."""
    url = f"{BASE}/api/v1/{path}" + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    out = None
    while url:
        with request(url) as r:
            data = json.loads(r.read())
            nxt = re.search(r'<([^>]+)>;\s*rel="next"', r.headers.get("Link", ""))
        if isinstance(data, list):
            out = (out or []) + data
        else:
            return data
        url = nxt.group(1) if nxt else None
    return out or []


def text(h: str) -> str:
    h = re.sub(r"<\s*(br|/p|/li|/h\d)\s*/?>", "\n", h or "", flags=re.I)
    return re.sub(r"[ \t]+", " ", html.unescape(re.sub(r"<[^>]+>", " ", h))).replace("\xa0", " ").strip()


def find_course(u: dict, courses: list) -> dict | None:
    if u.get("canvas_course_id"):
        return next((c for c in courses if c["id"] == int(u["canvas_course_id"])), None)
    return next((c for c in courses if str(c.get("course_code", "")).startswith(u["code"])), None)


def download(url: str, dst: Path) -> bool:
    if dst.exists():
        return False
    dst.parent.mkdir(parents=True, exist_ok=True)
    with request(url) as r:
        dst.write_bytes(r.read())
    return True


def is_lecture(name: str, title: str) -> bool:
    """Slides worth turning into recall material: judged by the file name (and 'slides' in the title)."""
    if not re.search(r"\.(pdf|pptx?)$", name, re.I) or re.search(r"map|timetable|room|roster|rubric", name, re.I):
        return False
    return bool(LECTURY.search(name) or re.search(r"\bslides?\b", title, re.I))


def pptx_to_pdf(src: Path) -> Path | None:
    """Convert with PowerPoint (COM) so the slides can go through the PDF pipeline."""
    import subprocess
    dst = src.with_suffix(".pdf")
    if dst.exists():
        return dst
    ps = ("$p = New-Object -ComObject PowerPoint.Application; "
          f"$d = $p.Presentations.Open('{src}', $true, $false, $false); $d.SaveAs('{dst}', 32); $d.Close(); $p.Quit()")
    subprocess.run(["powershell", "-NoProfile", "-Command", ps], capture_output=True, timeout=180)
    return dst if dst.exists() else None


def module_items(cid: int) -> list[tuple[str, dict]]:
    """(module name, item) for every item in the course's modules, in course order."""
    out = []
    for m in get(f"courses/{cid}/modules", per_page=100, include=["items"]):
        items = m.get("items")
        if items is None:                       # large modules: Canvas leaves the items out of the listing
            items = get(f"courses/{cid}/modules/{m['id']}/items", per_page=100)
        out += [(m["name"], it) for it in items]
    return out


def course_files(cid: int) -> tuple[list[tuple[str, dict]], int]:
    """Every file the modules offer: File items, and files linked inside module Pages (some units put their
    slides on weekly pages). Each file once, under the first module that has it. Also returns how many links
    Canvas refused (deleted or hidden files)."""
    seen, refused = {}, 0
    for module, it in module_items(cid):
        if it["type"] == "File":
            ids = [it["content_id"]]
        elif it["type"] == "Page" and it.get("page_url"):
            try:
                body = get(f"courses/{cid}/pages/{it['page_url']}").get("body") or ""
            except urllib.error.HTTPError:
                continue
            ids = re.findall(r"/files/(\d+)", body)
        else:
            continue
        for fid in dict.fromkeys(int(i) for i in ids):
            if fid in seen:
                continue
            try:
                seen[fid] = (module, get(f"courses/{cid}/files/{fid}"))
            except urllib.error.HTTPError:
                seen[fid], refused = None, refused + 1
    return [v for v in seen.values() if v], refused


def media_class(f: dict) -> str:
    """image / video / audio / other: Canvas's mime_class misses some videos (e.g. 'file' for an .mp4)."""
    ctype = (f.get("content-type") or "").split("/")[0]
    ext = Path(local_name(f)).suffix.lower()
    if f.get("mime_class") in SKIP_CLASSES or ctype in SKIP_CLASSES:
        return f.get("mime_class") if f.get("mime_class") in SKIP_CLASSES else ctype
    if ext in (".mp4", ".mov", ".m4v", ".avi", ".mkv", ".webm", ".wmv"):
        return "video"
    if ext in (".mp3", ".m4a", ".wav", ".aac", ".ogg"):
        return "audio"
    return "image" if ext in (".png", ".jpg", ".jpeg", ".gif", ".svg", ".webp", ".bmp") else "other"


def local_name(f: dict) -> str:
    """Canvas display name, made safe, with the real extension (display names sometimes drop it)."""
    name = re.sub(r'[<>:"/\\|?*]', "_", f.get("display_name") or f.get("filename") or str(f["id"])).strip()
    ext = Path(urllib.parse.unquote_plus(f.get("filename") or "")).suffix
    return name if not ext or name.lower().endswith(ext.lower()) else name + ext


def sync_module_files(u: dict, cid: int, videos: bool = False) -> dict:
    """Mirror the course's module files into <unit OneDrive folder>/canvas/<module>/.

    Every file is saved here, even when you have a copy elsewhere in the unit folder: since 2026-11-20 the mirror is
    the ground truth for lectures and tutorials, so it must be complete (files an older run skipped as "yours" are
    fetched on the next run). state/canvas/<id>/module_files.json remembers each Canvas file's version, so re-runs
    fetch only files changed on Canvas. Images (icons, staff photos) are skipped; videos and audio unless
    `videos`. Never deletes anything."""
    root = unit_dir(u) / CANVAS_DIR
    man_path = STATE / "canvas" / u["id"] / "module_files.json"
    man = json.loads(man_path.read_text("utf-8")) if man_path.exists() else {}
    files, refused = course_files(cid)
    rows, counts = [], {"new": 0, "updated": 0, "unchanged": 0, "skipped": 0, "failed": 0}
    for module, f in files:
        fid, stamp = str(f["id"]), f.get("updated_at") or f.get("modified_at") or ""
        rec = man.get(fid)
        row = {"module": module, "name": local_name(f), "size": f.get("size") or 0, "updated": stamp[:10]}
        skip = SKIP_CLASSES - ({"video", "audio"} if videos else set())
        if media_class(f) in skip or f.get("locked_for_user") or not f.get("url"):
            counts["skipped"] += 1
            continue
        if rec and rec.get("stamp") == stamp and rec.get("status") != "yours" and Path(rec["path"]).exists():
            counts["unchanged"] += 1
            rows.append({**row, "status": rec["status"], "path": rec["path"]})
            continue
        try:
            with request(f["url"]) as r:
                data = r.read()
        except Exception as e:
            counts["failed"] += 1
            rows.append({**row, "status": "failed", "path": "", "error": str(e)[:120]})
            continue
        path = root / re.sub(r'[<>:"/\\|?*]', "_", module).strip() / row["name"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        status = "updated" if rec and rec.get("status") != "yours" else "new"
        if path.suffix.lower() in (".pptx", ".ppt"):
            pptx_to_pdf(path)
        counts[status] += 1
        man[fid] = {"stamp": stamp, "status": "saved", "path": str(path)}
        rows.append({**row, "status": status, "path": str(path)})
    man_path.parent.mkdir(parents=True, exist_ok=True)
    man_path.write_text(json.dumps(man, indent=2, ensure_ascii=False), "utf-8")
    used = {p.resolve() for k in (u.get("sources") or {}) for p in find_sources(u, k)}
    for r in rows:
        r["in_sources"] = bool(r["path"]) and Path(r["path"]).resolve() in used
    return {"rows": rows, "counts": counts, "refused": refused}


def sync_unit(u: dict, course: dict, files: bool, videos: bool = False) -> dict:
    cid = course["id"]
    marked = []
    for a in get(f"courses/{cid}/assignments", per_page=100, include=["submission"]):
        s = a.get("submission") or {}
        comments = []
        if s.get("workflow_state") == "graded" or s.get("score") is not None:
            sub = get(f"courses/{cid}/assignments/{a['id']}/submissions/self", include=["submission_comments"])
            comments = [{"id": c["id"], "author": c.get("author_name"), "text": c.get("comment", "").strip(),
                         "date": (c.get("created_at") or "")[:10]}
                        for c in sub.get("submission_comments", []) if c.get("comment", "").strip()]
        marked.append({"id": a["id"], "name": a["name"], "points": a.get("points_possible"),
                       "score": s.get("score"), "state": s.get("workflow_state"), "late": s.get("late"),
                       "graded_at": (s.get("graded_at") or "")[:10], "due": (a.get("due_at") or "")[:10],
                       "comments": comments})
    anns = get("announcements", per_page=100, context_codes=[f"course_{cid}"],
               start_date="2026-01-01", end_date="2027-12-31")
    announcements, downloaded = [], []
    for an in anns:
        body = text(an.get("message", ""))
        atts = [{"name": x.get("display_name") or x.get("filename"), "url": x.get("url")} for x in an.get("attachments", [])]
        links = re.findall(r'href="([^"]*/files/(\d+)[^"]*)"', an.get("message", "") or "")
        for _, fid in links:                                      # files linked inline in the message
            try:
                f = get(f"courses/{cid}/files/{fid}")
                atts.append({"name": f.get("display_name"), "url": f.get("url")})
            except Exception:
                pass
        announcements.append({"id": an["id"], "title": an["title"], "posted": (an.get("posted_at") or "")[:10],
                              "author": (an.get("author") or {}).get("display_name"), "text": body,
                              "exam_related": bool(EXAMY.search(an["title"] + " " + body)), "files": atts})
        if files:
            for x in atts:
                if not x.get("url") or not x.get("name"):
                    continue
                lec = is_lecture(x["name"], an["title"])
                safe = re.sub(r'[<>:"/\\|?*]', "_", x["name"])
                if lec:                                           # name it after the announcement date
                    safe = f"Lecture {an.get('posted_at', '')[:10]} {safe}"
                dst = STATE / "canvas" / u["id"] / "files" / ("lectures" if lec else "other") / safe
                try:
                    if download(x["url"], dst):
                        downloaded.append(str(dst))
                    if lec and dst.suffix.lower() in (".pptx", ".ppt"):
                        pdf = pptx_to_pdf(dst)
                        if pdf:
                            dst = pdf
                    x["local"] = str(dst)
                except Exception as e:
                    x["error"] = str(e)[:120]
    return {"course": {"id": cid, "code": course.get("course_code"), "name": course.get("name")},
            "fetched": datetime.now(TZ).isoformat(timespec="seconds"), "assignments": marked,
            "announcements": announcements, "downloaded": downloaded,
            "module_files": sync_module_files(u, cid, videos) if files else None}


def pct(a) -> str:
    return f"{100 * a['score'] / a['points']:.0f}%" if a.get("score") is not None and a.get("points") else ""


def render(u: dict, d: dict) -> str:
    c = d["course"]
    L = ["---", f"unit: {u['code']}", "type: canvas record", "source: canvas", f"course: {c['code']} ({c['id']})", f"fetched: {d['fetched'][:16]}",
         "generator: pipeline_academic/scripts/canvas_sync.py", "---", "", f"# {u['code']}: Canvas", "",
         f"[Open course]({BASE}/courses/{c['id']})", "", "## Marked work",
         "| Assessment | Score | Out of | % | Graded | Marker comment |", "|---|---|---|---|---|---|"]
    for a in d["assignments"]:
        if a["score"] is None and a["state"] not in ("submitted", "pending_review"):
            continue
        cm = " / ".join(x["text"].replace("\n", " ").replace("|", "/") for x in a["comments"]) or ""
        sc = "—" if a["score"] is None else f"{a['score']:g}"
        L.append(f"| {a['name']} | {sc} | {a['points'] or 0:g} | {pct(a)} | {a['graded_at'] or a['state']} | {cm} |")
    lost = [(a, x) for a in d["assignments"] for x in a["comments"]
            if a.get("score") is not None and a.get("points") and a["score"] < a["points"]]
    if lost:
        L += ["", "## Where marks were lost", "> [!trap] These are your real weak spots: revise them, and expect "
              "similar questions. Each one is also in `practice/error_log.md`."]
        L += [f"- **{a['name']}** ({a['score']:g}/{a['points']:g}): {x['text']}" for a, x in lost]
    ex = [a for a in d["announcements"] if a["exam_related"]]
    if ex:
        L += ["", "## Exam-related announcements (full text)"]
        for a in ex:
            L += ["", f"### {a['posted']}: {a['title']}", "", a["text"]]
            L += [f"- Attachment: {f['name']}" + (f" (saved: `{f['local']}`)" if f.get("local") else "") for f in a["files"]]
    other = [a for a in d["announcements"] if not a["exam_related"]]
    if other:
        L += ["", "## Other announcements"] + [f"- {a['posted']}: {a['title']}" for a in other]
    mf = d.get("module_files")
    if mf and mf["rows"]:
        base = unit_dir(u)
        rel = lambda p: Path(p).relative_to(base).as_posix() if Path(p).is_relative_to(base) else Path(p).name
        missing = [r for r in mf["rows"] if r["status"] in ("new", "updated", "saved") and not r["in_sources"]]
        L += ["", "## Course files (Canvas modules)",
              f"Mirrored into the unit's OneDrive folder under `{CANVAS_DIR}/<module>/`: the ground truth for "
              "lectures and tutorials. Images and videos are not mirrored (`canvas_sync.py --videos` adds videos)."]
        if missing:
            L += ["", "> [!todo] Saved from Canvas but not in the pipeline's sources (no recall material, mocks or "
                  "handbook use them yet). Add a glob to `sources:` in the unit config for the ones that matter:"]
            L += [f"> - `{rel(r['path'])}` (updated {r['updated']})" for r in missing]
        module = None
        for r in mf["rows"]:
            if r["module"] != module:
                module = r["module"]
                L += ["", f"### {module}"]
            where = {"yours": "you have it: ", "failed": "FAIL download: "}.get(r["status"], "saved: ")
            L.append(f"- {r['name']} ({r['size'] / 2**20:.1f} MB, {r['updated']}): {where}"
                     + (f"`{rel(r['path'])}`" if r["path"] else r.get("error", "")))
    return "\n".join(L) + "\n"


def seed_error_log(u: dict, d: dict) -> int:
    """Append each marker comment on a lost-mark assessment to error_log.md, once (tracked by comment id)."""
    log = unit_path(u, "error_log")
    if not log.exists():
        return 0
    text_ = log.read_text("utf-8")
    new = []
    for a in d["assignments"]:
        if a.get("score") is None or not a.get("points") or a["score"] >= a["points"]:
            continue
        for x in a["comments"]:
            if f"canvas:{x['id']}" in text_:
                continue
            new.append(f"- {x['date']} · **{a['name']}** ({a['score']:g}/{a['points']:g}), marker: {x['text']} "
                       f"<!-- canvas:{x['id']} -->")
    if new:
        head = "" if "## From Canvas marker feedback" in text_ else "\n\n## From Canvas marker feedback (appended by canvas_sync.py)\n"
        log.write_text(text_.rstrip() + head + "\n" + "\n".join(new) + "\n", "utf-8")
    return len(new)


def compare_assumptions(u: dict, d: dict) -> list[str]:
    """Tasks the unit config still assumes a mark for, next to what Canvas actually has."""
    out = []
    for task, frac in (u.get("assumptions") or {}).items():
        words = set(re.findall(r"[a-z]+|\d+", task.lower())) - {"report", "the"}
        best = max(d["assignments"], default=None,
                   key=lambda a: len(words & set(re.findall(r"[a-z]+|\d+", a["name"].lower()))))
        if not best or not words & set(re.findall(r"[a-z]+|\d+", best["name"].lower())):
            continue
        if best.get("score") is not None:
            out.append(f"assumed {task!r} = {frac:.0%}; Canvas '{best['name']}' = {best['score']:g}/{best['points']:g} "
                       f"({pct(best)}) → put the real mark in results.xlsx and drop the assumption")
        else:
            out.append(f"assumed {task!r} = {frac:.0%}; Canvas '{best['name']}' has no visible score yet ({best['state']})")
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--no-files", action="store_true", help="don't download announcement attachments or module files")
    ap.add_argument("--videos", action="store_true", help="also mirror module videos and audio (large)")
    args = ap.parse_args()
    courses = get("courses", enrollment_state="active", per_page=100)
    for u in work_units(args.unit, args.allow_sat):
        course = find_course(u, courses)
        if not course:
            print(f"  FAIL {u['code']}: no active Canvas course with that code (set canvas_course_id)")
            continue
        try:
            d = sync_unit(u, course, not args.no_files, args.videos)
        except Exception as e:
            print(f"  FAIL {u['code']}: {str(e)[:200]}")
            continue
        out = STATE / "canvas" / f"{u['code']}.json"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text(json.dumps(d, indent=2, ensure_ascii=False), "utf-8")
        unit_path(u, "canvas").parent.mkdir(parents=True, exist_ok=True)
        unit_path(u, "canvas").write_text(render(u, d), "utf-8")
        n = seed_error_log(u, d)
        graded = [a for a in d["assignments"] if a["score"] is not None]
        print(f"  ok {u['code']} ({course['course_code']}): {len(graded)} marked, "
              f"{sum(len(a['comments']) for a in graded)} marker comments ({n} new in error_log), "
              f"{len(d['announcements'])} announcements ({sum(a['exam_related'] for a in d['announcements'])} exam-related), "
              f"{len(d['downloaded'])} new file(s)")
        for f in d["downloaded"]:
            print(f"      + {Path(f).relative_to(STATE)}")
        mf = d.get("module_files")
        if mf:
            c = mf["counts"]
            print(f"      module files: {c['new']} new, {c['updated']} updated on Canvas, "
                  f"{c['unchanged']} unchanged, {c['skipped']} skipped (images/videos), {c['failed']} failed"
                  + (f", {mf['refused']} link(s) Canvas refused" if mf["refused"] else ""))
            for r in mf["rows"]:
                if r["status"] in ("new", "updated"):
                    note = "" if r["in_sources"] else "  (not in the pipeline's sources)"
                    print(f"      + {r['status']}: {Path(r['path']).relative_to(unit_dir(u))}{note}")
        for line in compare_assumptions(u, d):
            print(f"      WARN {line}")


if __name__ == "__main__":
    main()
