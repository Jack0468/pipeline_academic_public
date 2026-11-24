"""One place that says what the pipeline reads for a unit: every input, where it lives, and who uses it.

The ground truth for files is the unit config's `sources:` globs (one glob list per kind). This module resolves
them, plus the vault notes the generators read, the same way for every generator. It also checks them for
the breakages seen so far:
- fewer lecture PDFs than recall files: files moved, and a glob no longer finds them (2026-11-17 and -18);
- two PDFs for one lecture number (L2a/L2b), which orphans recall files;
- a glob that matches nothing;
- handwritten PDFs with no transcription yet;
- intel items newer than exam_intel.md.
It also prints the PDFs in the unit folder that no glob covers, so nothing is missed silently.

gen_practice_exam.py builds its inputs from resolve() and refuses to write a mock while a critical problem stands
(--ignore-input-check overrides).

    python scripts/unit_inputs.py --unit ABCD1002
"""
from __future__ import annotations

import argparse
import glob
import sys
from dataclasses import dataclass, field
from pathlib import Path

from common import unit_path, STATE, expand, file_hash, find_sources, lecture_labels, load_units, scope_files, unit_dir, vault_unit_dir
from common import doc_relation, name_key, transcription_for, tutorial_solutions



OPTIONAL_KINDS = {"recall_attempts", "practice_attempts"}


@dataclass
class Inputs:
    scope: list[Path]
    lectures: list[Path]
    practice: list[Path]
    tutorial_solutions: list[Path]        # the course's own (common.tutorial_solutions: files that hold answers)
    own_work: list[Path]
    handwritten_pdfs: list[Path]          # what extract_handwriting.py transcribes
    transcriptions: list[Path]            # vault sources/handwritten_*.md: what the generators read
    recall: list[Path]
    reference: list[Path]                 # third-party study notes (xtra_resources/ by default)
    intel_items: list[Path]
    notes: dict[str, Path | None] = field(default_factory=dict)   # outline, canvas, intel, handbook (vault)

    def rows(self) -> list[tuple[str, list[Path], str]]:
        """(kind, files, used by) for printing."""
        note = lambda k: [self.notes[k]] if self.notes.get(k) else []
        return [
            ("scope (official exam documents)", self.scope, "mocks, reconstructions, intel check"),
            ("lectures", self.lectures, "recall files, mocks, reconstructions, handbook figures"),
            ("practice (tutorials, quizzes, labs)", self.practice, "mocks (style, no repeats)"),
            ("tutorial solutions (course's own)", self.tutorial_solutions, "tutorial index (after your attempt), worked examples"),
            ("own work", self.own_work, "mocks (re-test what you got wrong)"),
            ("reference notes (third-party)", self.reference, "mocks; handbook backbone via convert_reference.py"),
            ("handwritten PDFs", self.handwritten_pdfs, "extract_handwriting.py"),
            ("handwritten transcriptions", self.transcriptions, "mocks (rank 1)"),
            ("recall files", self.recall, "mocks (already practised), handbook, intel mapping"),
            ("intel items", self.intel_items, "ingest_intel.py"),
            ("exam intel note", note("intel"), "mocks, handbook draft"),
            ("unit outline", note("outline"), "mocks, reconstructions, intel check"),
            ("canvas.md", note("canvas"), "mocks (announcements, marker feedback), intel check"),
            ("handbook", note("handbook"), "reconstructions"),
        ]


def resolve(u: dict) -> Inputs:
    vnote = lambda key: unit_path(u, key) if unit_path(u, key).exists() else None
    return Inputs(
        scope=scope_files(u),
        lectures=find_sources(u, "lectures"),
        practice=find_sources(u, "practice"),
        tutorial_solutions=[p for p, _ in tutorial_solutions(u)],
        own_work=find_sources(u, "own_work"),
        handwritten_pdfs=find_sources(u, "handwritten"),
        transcriptions=sorted(unit_path(u, "sources").glob("handwritten_*.md")),
        recall=sorted(unit_path(u, "recall").glob("*.md")),
        reference=find_sources(u, "reference"),
        intel_items=[p for p in find_sources(u, "intel")
                     if p.name.lower() != "desktop.ini" and not p.name.startswith(("~$", "."))],
        notes={key: vnote(key) for key in ("intel", "outline", "canvas", "handbook")},
    )


def problems(u: dict, inp: Inputs) -> tuple[list[str], list[str]]:
    """(critical, warnings). Critical problems would make a mock (or recall files) silently wrong."""
    crit, warn = [], []
    labels = lecture_labels(inp.lectures)
    if inp.recall and len(inp.lectures) < len(inp.recall):
        crit.append(f"only {len(inp.lectures)} lecture PDF(s) found, but {len(inp.recall)} recall files exist: lecture "
                    "files have probably moved. Fix sources.lectures in the unit config (python scripts/unit_inputs.py "
                    f"--unit {u['code']} shows what each glob finds)")
    dup = sorted({v[:-1] for v in labels.values() if v[-1:] in "abcdef" and v[:-1].startswith("L") and v[1:-1].isdigit()})
    recall_labels = {p.stem.split("_")[-1] for p in inp.recall}
    for lab in dup:
        if lab in recall_labels:                 # a single lecture now found twice (L2 → L2a + L2b)
            files = [p for p, v in labels.items() if v.startswith(lab) and v[len(lab):] in "abcdef"]
            crit.append(f"{lab} is found {len(files)} times ({', '.join(rel(u, p) for p in files)}): keep one source, "
                        "or its recall file is orphaned and the copies get regenerated as separate lectures")
    for kind, pats in (u.get("sources") or {}).items():
        if kind in OPTIONAL_KINDS:               # empty until you scan an attempt: normal
            continue
        for pat in pats or []:
            if "${STATE}" in pat:                # filled by scripts when there is something (e.g. announcement slides)
                continue
            if not glob.glob(str(unit_dir(u) / expand(pat)), recursive=True):
                warn.append(f"sources.{kind}: '{pat}' matches no file")
    for p in inp.handwritten_pdfs:
        if not transcription_for(u, p).exists():                  # by its `original:` line, so a renamed one counts
            warn.append(f"handwritten {rel(u, p)} has no transcription yet: run scripts/extract_handwriting.py --unit {u['code']}")
    note = inp.notes.get("intel")
    if inp.intel_items and (not note or max(p.stat().st_mtime for p in inp.intel_items) > note.stat().st_mtime):
        warn.append(f"intel items are newer than {unit_path(u, 'intel').name} (or it is missing): run scripts/ingest_intel.py --unit {u['code']}")
    return crit, warn


def uncovered(u: dict, inp: Inputs) -> list[Path]:
    """PDFs in the unit's OneDrive folder that no source glob covers (excluding the Canvas mirror's leftovers,
    which canvas.md lists)."""
    used = {p.resolve() for p in inp.scope + inp.lectures + inp.practice + inp.tutorial_solutions + inp.own_work + inp.handwritten_pdfs
            + inp.reference + inp.intel_items}
    for kind in ("recall_attempts", "practice_attempts"):
        used |= {p.resolve() for p in find_sources(u, kind)}
    def h(p: Path) -> str | None:
        try:
            return file_hash(p)
        except OSError:                          # locked (open elsewhere) or not synced from OneDrive: list it
            return None
    hashes = {h(p) for p in used} - {None}
    base = unit_dir(u)
    return sorted(p for p in base.rglob("*.pdf") if p.resolve() not in used and not {"canvas", "_superseded"} & set(p.relative_to(base).parts)
                  and h(p) not in hashes)          # an identical copy of a file that is read counts as read


def rel(u: dict, p: Path) -> str:
    for base, tag in ((unit_dir(u), ""), (vault_unit_dir(u), "vault:"), (STATE, "state:")):
        try:
            return tag + p.relative_to(base).as_posix()
        except ValueError:
            pass
    return str(p)


def report(u: dict, inp: Inputs | None = None, files: bool = True) -> str:
    inp = inp or resolve(u)
    L = [f"{u['code']}: inputs (ground truth: config/units/{u['id']}.yaml sources:, plus vault notes)"]
    labels = lecture_labels(inp.lectures)
    for kind, paths, used in inp.rows():
        L.append(f"  {kind}: {len(paths)}  -> {used}")
        if files:
            for p in paths:
                L.append(f"      {labels.get(p, '') + ' ' if kind == 'lectures' else ''}{rel(u, p)}")
    crit, warn = problems(u, inp)
    L += [f"  CRITICAL {m}" for m in crit] + [f"  WARN {m}" for m in warn]
    left = uncovered(u, inp)
    if left:
        L.append(f"  not read by the pipeline ({len(left)} PDF(s) outside every source glob; fine if intended):")
        L += [f"      {rel(u, p)}" for p in left]
    same = same_name(inp.scope + inp.lectures + inp.practice + inp.tutorial_solutions + inp.own_work
                     + inp.reference + left)
    if same:
        L.append(f"  same name, checked for what really differs ({len(same)} group(s); never assume they are the same "
                 "document):")
        for files, rels in same:
            L.append("      " + " | ".join(rel(u, p) for p in files))
            L += [f"        -> {r}" for r in rels]
    return "\n".join(L)


def same_name(paths: list[Path]) -> list[tuple[list[Path], list[str]]]:
    """Groups of files that share a name (common.name_key: Canvas's -N suffixes ignored) but not their bytes, each
    with what really differs: a question sheet and its solutions, another version, or a different document."""
    groups: dict[str, list[Path]] = {}
    for p in dict.fromkeys(x for x in paths if x.suffix.lower() == ".pdf"):
        groups.setdefault(name_key(p), []).append(p)
    out = []
    for files in groups.values():
        if len(files) < 2:
            continue
        first = files[0]
        rels = [f"{p.name} vs {first.name}: {doc_relation(first, p)}" for p in files[1:]]
        if any(not r.endswith("identical copies") for r in rels):
            out.append((files, rels))
    return out


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--brief", action="store_true", help="counts and problems only, no file lists")
    a = ap.parse_args()
    for u in load_units(a.unit):
        print(report(u, files=not a.brief) + "\n")


if __name__ == "__main__":
    main()
