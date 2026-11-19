"""Alphabetical index and page furniture for the printed exam handbook (build_handbook.py --pdf).

Every content line of handbook.md gets a LaTeX \\label (gl<line>), so after the compile the .aux file
gives the exact printed page of each line. From that:
  - index terms come from the handbook itself (no AI): section titles, "Use when" triggers, Lookup
    phrases, the concepts and constants tables, proper names (Friis, Babinet ...) and acronyms (HPBW,
    VSWR ...), plus any "see" cross-references in the unit config (print.see);
  - every page gets a running header (which sections it holds) and an edge tab (which group).
Works for any unit whose handbook follows the "## <n>. <title>" section format.
"""
from __future__ import annotations

import re
import subprocess
from collections import defaultdict
from pathlib import Path

HEAD = re.compile(r"^## (.*?)\s*$")
NUM = re.compile(r"^(\d+)\.\s*(.*)$")
LREF = re.compile(r"\(\s*(L\d+[a-z]?)\s+pp?\.\s*(\d[\d,\s\u2013\-]*)\)")

STOP_CAPS = {"NOT", "AND", "OR", "ALWAYS", "NEVER", "ONLY", "NO", "DO", "ALL", "USE", "THE", "IS", "IF",
             "MUST", "TRAP", "ONE", "TWO", "BOTH", "NOTE", "CHECK", "THEN", "FIRST", "OF", "IN", "TO",
             "MHz", "GHz", "THz", "kHz", "dBW", "dBi", "dBd", "dBm", "AWGN"}
STOP_NAMES = {"Use", "Steps", "Trap", "Section", "Lecture", "Lectures", "Question", "Option", "Note",
              "Check", "Then", "For", "The", "This", "When", "With", "Not", "Always", "Never", "Only",
              "Add", "See", "Go", "If", "In", "On", "At", "By", "To", "From", "And", "Or", "But", "Both",
              "Each", "All", "Any", "One", "Two", "Do", "Is", "Are", "Use", "Compute", "Find", "Calculate",
              "Identify", "Apply", "Set", "Solve", "Convert", "Read", "Pick", "Choose", "Determine",
              "Evaluate", "Recall", "Substitute", "Normal", "Table", "Figure", "Fig", "Front", "Concepts", "Pos",
              # unit names are not index terms
              "Degree", "Degrees", "Watt", "Watts", "Ohm", "Ohms", "Kelvin", "Hertz", "Volt", "Volts", "Joule",
              "Joules", "Tesla", "Henry", "Farad", "Siemens", "Ampere", "Amperes", "Coulomb", "Decibel"}
FOLLOW_STOP = {"and", "the", "for", "with", "is", "to", "of", "in", "at", "or", "on", "by", "as", "from", "are"}
GENERIC = {"antenna", "antennas", "system", "systems", "method", "methods", "example", "examples",
           "design", "reference", "propagation", "measurements", "parameters", "fields", "fields regions",
           "theory", "review", "basics", "formulas", "core exam formulas", "comprehensive review"}


# --- text helpers -------------------------------------------------------------------------------

def plain(s: str) -> str:
    """Readable plain text from Markdown + TeX maths (captions, headers, sort keys)."""
    s = re.sub(r"\$([^$]*)\$", lambda m: re.sub(r"\\(mathrm|text|mathbf|boldsymbol)\{([^}]*)\}", r"\2",
                                                 m.group(1)), s)
    s = re.sub(r"\\([A-Za-z]+)", r"\1", s).replace("{", "").replace("}", "").replace("_", "").replace("^", "")
    s = s.replace("**", "").replace("*", "")
    return re.sub(r"\s+", " ", s).strip()


def sort_key(term: str) -> str:
    k = plain(term).lower()
    k = re.sub(r"^(the|a|an)\s+", "", k)
    return re.sub(r"[^a-z0-9 ]", "", k).strip()


def no_math(s: str) -> str:
    return re.sub(r"\$[^$]*\$", " ", s)


def split_top(s: str, seps: str = ",;:&") -> list[str]:
    """Split on separators that are outside $...$ and (...)."""
    out, cur, depth, math = [], [], 0, False
    for ch in s:
        if ch == "$":
            math = not math
        elif not math and ch == "(":
            depth += 1
        elif not math and ch == ")":
            depth = max(0, depth - 1)
        if ch in seps and not math and depth == 0:
            out.append("".join(cur))
            cur = []
        else:
            cur.append(ch)
    out.append("".join(cur))
    return [x.strip() for x in out if x.strip()]


def drop_parens(s: str) -> str:
    out, depth, math = [], 0, False
    for ch in s:
        if ch == "$":
            math = not math
        if not math and ch == "(":
            depth += 1
            continue
        if not math and ch == ")":
            depth = max(0, depth - 1)
            continue
        if depth == 0:
            out.append(ch)
    return re.sub(r"\s+", " ", "".join(out)).strip()


def cells(row: str) -> list[str]:
    return [c.strip() for c in row.strip().strip("|").split("|")]


def is_sep(row: str) -> bool:
    return bool(re.match(r"^\|[\s:|\-]+\|\s*$", row))


# --- page map from the LaTeX .aux ----------------------------------------------------------------

def _groups(s: str, i: int, n: int) -> list[str]:
    out = []
    while len(out) < n and i < len(s):
        while i < len(s) and s[i] != "{":
            i += 1
        depth, j = 0, i
        while j < len(s):
            depth += {"{": 1, "}": -1}.get(s[j], 0)
            if depth == 0:
                break
            j += 1
        out.append(s[i + 1:j])
        i = j + 1
    return out


def label_pages(aux: Path) -> dict[int, int]:
    """gl<line> label → printed page."""
    out = {}
    for line in aux.read_text("utf-8", errors="replace").splitlines():
        g = re.match(r"\\glpage\{(\d+)\}\{(\d+)\}", line)
        if g:
            out.setdefault(int(g.group(1)), int(g.group(2)))     # first page the line appears on
            continue
        m = re.match(r"\\newlabel\{gl(\d+)\}\{", line)
        if m:
            g = _groups(line, m.end() - 1, 1)
            if g:
                inner = _groups(g[0], 0, 2)
                if len(inner) == 2 and inner[1].strip().isdigit():
                    out[int(m.group(1))] = int(inner[1])
    return out


# --- handbook structure ---------------------------------------------------------------------------

def sections(lines: list[str]) -> list[dict]:
    """Headings in order: key ('F' front, '<n>' numbered, 'T<i>' tail), title, line index."""
    out, seen_num = [], False
    for i, ln in enumerate(lines):
        h = HEAD.match(ln)
        if not h:
            continue
        t = h.group(1)
        m = NUM.match(t)
        if m:
            seen_num = True
            out.append(dict(key=m.group(1), title=re.sub(r"\s*\(L[^)]*\)\s*$", "", m.group(2)).strip(), line=i))
        else:
            out.append(dict(key=("T" if seen_num else "F") + str(i), title=t.strip(), line=i))
    return out


def section_of(secs: list[dict], i: int) -> dict | None:
    cur = None
    for s in secs:
        if s["line"] <= i:
            cur = s
    return cur


def page_of(line_pages: dict[int, int], i: int) -> int | None:
    """Printed page of a line; lines without a label (display maths) take the nearest label above."""
    for j in range(i, -1, -1):
        if j in line_pages:
            return line_pages[j]
    return None


# --- index ----------------------------------------------------------------------------------------

class Index:
    def __init__(self):
        self.e: dict[str, dict] = {}

    def add(self, term: str, pages=(), bold=(), ital=(), see: str | None = None, we=()):
        term = re.sub(r"\s+", " ", term.replace("**", "")).strip(" .;:,-\u2013")
        if term.count("$") % 2 or not term:
            return
        k = sort_key(term)
        if len(k) < 2 or len(plain(term)) > 64 or k in GENERIC:
            return
        if not see and not (pages or bold or ital or we):
            return
        first = re.match(r"[A-Za-z]+", term)
        first = first.group(0) if first else ""
        if re.fullmatch(r"[A-Z][a-z]+", first):
            if first.lower() in self.lower_words:                 # a common word: index style is lowercase
                term = term[0].lower() + term[1:]
        d = self.e.setdefault(k, dict(term=term, pages=set(), bold=set(), ital=set(), we=set(), see=None))
        d["pages"] |= {p for p in pages if p}
        d["bold"] |= {p for p in bold if p}
        d["ital"] |= {p for p in ital if p}
        d["we"] |= {p for p in we if p}
        if see:
            d["see"] = see

    lower_words: set[str] = set()


def build(lines: list[str], line_pages: dict[int, int], fig_pages: dict[tuple[str, int], int],
          sec_figs: dict[str, list[int]], see: dict | None = None, terms: list | None = None) -> Index:
    secs = sections(lines)
    num = {s["key"]: s for s in secs if s["key"].isdigit()}
    home = {k: page_of(line_pages, s["line"]) for k, s in num.items()}
    skip_lines = set()                                     # Lookup table and the to-do list point elsewhere
    for a, s in enumerate(secs):
        if re.match(r"(lookup|index phrases|to add)", s["title"], re.I):
            end = secs[a + 1]["line"] if a + 1 < len(secs) else len(lines)
            skip_lines |= set(range(s["line"], end))

    def figs_on(i):
        return [fig_pages[(lab, p)] for lab, spec in LREF.findall(lines[i]) for p in _pages(spec)
                if (lab, p) in fig_pages][:2]

    # proper names and acronyms, counted outside maths, refs and table headers. A word that also occurs
    # in lowercase is an ordinary word (capitalised at a line start, or shouted): not a name or acronym.
    texts = [LREF.sub(" ", no_math(ln)).replace("**", " ") for ln in lines]
    Index.lower_words = {w for t in texts for w in re.findall(r"\b[a-z][a-z]+\b", t)}
    caps, names = defaultdict(set), defaultdict(set)
    follow = defaultdict(lambda: defaultdict(int))         # name -> next word counts ("Smith" -> "chart")
    subscripts = {w for ln in lines for w in re.findall(r"_\{?\\(?:mathrm|text)\{([A-Za-z]{2,})\}", ln)}
    for i, t in enumerate(texts):
        if i in skip_lines or HEAD.match(lines[i]) or is_sep(lines[i]):
            continue
        for w in re.findall(r"\b(?:[A-Z]{2,7}s?|[A-Z][a-z][A-Z])\b", t):
            if w not in STOP_CAPS and w.lower().rstrip("s") not in Index.lower_words and w.lower() not in Index.lower_words:
                caps[w].add(i)
        for m in re.finditer(r"(?<=[a-z,(] )([A-Z][a-z]{2,}(?:[\u2013\-][A-Z][a-z]+)?)(?:\u2019s|'s)?\b", t):
            if m.group(1) not in STOP_NAMES and m.group(1).lower() not in Index.lower_words:
                names[m.group(1)].add(i)
                nxt = re.match(r"\s+([a-z]{3,})", t[m.end():])
                word = nxt.group(1) if nxt and nxt.group(1) not in FOLLOW_STOP else ""
                follow[m.group(1)][(m.group(0)[len(m.group(1)):] + " " + word) if word else ""] += 1
    # acronyms that only appear as maths subscripts (theta_HP, L_FS) are labels, not terms
    caps = {w: occ for w, occ in caps.items() if (len(w) > 2 or len(occ) >= 3) and w not in subscripts}
    # a name usually followed by the same word is indexed with it: "Smith chart", "Kraus formula"
    named = {}
    for w, occ in names.items():
        word, n = max(follow[w].items(), key=lambda kv: kv[1]) if follow[w] else ("", 0)
        named[f"{w}{word}" if word and n >= max(1, sum(follow[w].values()) / 2) else w] = occ
    ix = Index()

    for k, s in num.items():
        f = sec_figs.get(k, [])[:1]
        parts = split_top(s["title"])
        if len(parts) > 1 and len(s["title"]) <= 64:
            ix.add(drop_parens(s["title"]), bold=[home[k]], ital=f)     # the whole title too
        for ph in parts:
            ph = drop_parens(ph)
            ix.add(ph, bold=[home[k]], ital=f)
    for i, ln in enumerate(lines):
        if i in skip_lines:
            continue
        s = section_of(secs, i)
        if s and s["key"].isdigit() and ln.startswith("**Use when:**"):
            for ph in split_top(ln[len("**Use when:**"):]):
                ph = drop_parens(ph)
                if len(plain(ph).split()) <= 5:            # long trigger phrases clutter the index
                    ix.add(ph, bold=[page_of(line_pages, i)])
    # Lookup phrases → the target section's page
    for s in secs:
        if re.match(r"(lookup|index phrases)", s["title"], re.I):      # exam phrasings (not printed)
            for i in range(s["line"] + 1, len(lines)):
                if HEAD.match(lines[i]):
                    break
                header = i + 1 < len(lines) and is_sep(lines[i + 1])     # a row at the end of the file counts too
                if lines[i].startswith("|") and not is_sep(lines[i]) and not header:
                    c = cells(lines[i])
                    m = re.match(r"(\d+)\.", c[1]) if len(c) > 1 else None
                    if m and m.group(1) in home:
                        for ph in split_top(c[0]):
                            ix.add(drop_parens(ph), bold=[home[m.group(1)]])
    # concepts and constants tables: first cell is the term; maths in parentheses → symbol entry
    for s in secs:
        if not re.match(r"(concepts|constants)", s["title"], re.I):
            continue
        for i in range(s["line"] + 1, len(lines)):
            if HEAD.match(lines[i]):
                break
            ln = lines[i]
            if not ln.startswith("|") or is_sep(ln) or (i + 1 < len(lines) and is_sep(lines[i + 1])):
                continue
            term = cells(ln)[0]
            p = page_of(line_pages, i)
            ix.add(drop_parens(term), bold=[p], ital=figs_on(i))
            for sym in re.findall(r"\((\$[^$]+\$)\)", term):
                ix.add(sym, bold=[p])
    for s in secs:                                         # "Concepts in depth": each concept's name line
        if s["title"].lower().startswith("concepts in depth"):
            for i in range(s["line"] + 1, len(lines)):
                if HEAD.match(lines[i]):
                    break
                m = re.fullmatch(r"\*\*([^*:]+)\*\*\s*", lines[i])
                if m:
                    ix.add(drop_parens(m.group(1)), bold=[page_of(line_pages, i)])
    for w, occ in list(caps.items()) + list(named.items()):
        pages = sorted({page_of(line_pages, i) for i in occ} - {None})
        bold = [home[k] for k, s in num.items() if re.search(rf"\b{re.escape(w)}\b", s["title"])]
        ix.add(w, pages=pages[:8], bold=bold)
    for phrase, target in (see or {}).items():
        ix.add(str(phrase), see=str(target))
    # extra terms from the unit config (print.terms): every line that mentions them
    plains = [plain(ln).lower() for ln in lines]
    for term in terms or []:
        hit = [i for i, pl in enumerate(plains) if i not in skip_lines and str(term).lower() in pl]
        pages = sorted({page_of(line_pages, i) for i in hit} - {None})
        if pages:
            ix.add(str(term), pages=pages[:6])
    return ix


def _pages(spec: str) -> list[int]:
    out = []
    for part in re.split(r"\s*,\s*", spec.strip(" ,")):
        m = re.match(r"(\d+)\s*[\u2013\-]\s*(\d+)$", part)
        if m:
            a, b = int(m.group(1)), int(m.group(2))
            out += list(range(a, min(b, a + 2) + 1))
        elif part.isdigit():
            out.append(int(part))
    return out


def to_tex(terms: list[str]) -> list[str]:
    """Markdown fragments (with $maths$) → LaTeX, through pandoc in one call."""
    sep = "\n\nZZIDXSEPZZ\n\n"
    r = subprocess.run(["pandoc", "-f", "markdown+tex_math_dollars", "-t", "latex"], input=sep.join(terms),
                       capture_output=True, text=True, encoding="utf-8")
    if r.returncode:
        raise RuntimeError("pandoc failed on index terms: " + r.stderr[-800:])
    parts = [p.strip().replace("\n", " ") for p in r.stdout.split("ZZIDXSEPZZ")]
    return parts if len(parts) == len(terms) else [plain(t) for t in terms]


def render(ix: Index) -> str:
    """Index body: letter groups (symbols first), entries 'term, 12, **14**, *31*'."""
    items = sorted(ix.e.items())
    disp = to_tex([d["term"] for _, d in items])
    by_letter: dict[str, list[str]] = defaultdict(list)
    for (k, d), tex in zip(items, disp):
        letter = k[0].upper() if k[0].isalpha() and not d["term"].startswith("$") else "Symbols and numbers"
        if d["see"]:
            body = f"\\emph{{see}} {to_tex([d['see']])[0]}"
        else:
            pages = sorted(d["pages"] | d["bold"] | d["ital"])
            keep = [p for p in pages if p in d["bold"] or p in d["ital"]]
            pages = sorted(set(keep) | set([p for p in pages if p not in keep][:max(0, 6 - len(keep))]))
            body = ", ".join(f"\\textbf{{{p}}}" if p in d["bold"] else f"\\emph{{{p}}}" if p in d["ital"]
                             else str(p) for p in pages)
            if d["we"]:                                     # worked-example pages of the combined print
                body = ", ".join(x for x in [body, ", ".join(f"WE~{p}" for p in sorted(d["we"]))] if x)
        by_letter[letter].append(f"\\idxentry{{{tex}}}{{{body}}}")
    order = (["Symbols and numbers"] if "Symbols and numbers" in by_letter else []) + \
            sorted(x for x in by_letter if x != "Symbols and numbers")
    return "\n".join(f"\\idxletter{{{L}}}\n" + "\n".join(by_letter[L]) for L in order)


# --- edge tabs and running headers (drawn with PyMuPDF on the merged PDF) -------------------------

PALETTE = [(0.16, 0.32, 0.55), (0.62, 0.22, 0.20), (0.20, 0.47, 0.30), (0.50, 0.33, 0.58),
           (0.72, 0.45, 0.10), (0.15, 0.48, 0.52), (0.55, 0.25, 0.40), (0.35, 0.40, 0.18),
           (0.30, 0.30, 0.30), (0.45, 0.30, 0.15), (0.20, 0.25, 0.45), (0.40, 0.15, 0.15)]


def tab_groups(cfg_tabs, sec_keys: list[str]) -> list[tuple[str, set[str]]]:
    """[(label, {section keys})]: Constants (front), the topic groups (config or auto by 5), Concepts, Figures, Index."""
    nums = [k for k in sec_keys if k.isdigit()]
    groups = [("Constants", {k for k in sec_keys if k.startswith("F")})]   # front sections (no Lookup since 2026-11-18)
    if cfg_tabs:
        for label, a, b in cfg_tabs:
            groups.append((str(label), {k for k in nums if int(a) <= int(k) <= int(b)}))
    else:
        for j in range(0, len(nums), 5):
            chunk = nums[j:j + 5]
            groups.append((f"\u00a7{chunk[0]}\u2013{chunk[-1]}", set(chunk)))
    groups.append(("Concepts", {k for k in sec_keys if k.startswith("T")}))
    groups += [("Figures", {"FIG"}), ("Index", {"IDX"})]
    return [g for g in groups if g[1]]


def decorate(pdf_path: Path, out: Path, info: list[tuple[str, str]], groups: list[tuple[str, set[str]]],
             title: str, fig_range: tuple[int, int] | None = None, duplex: bool = True) -> None:
    """Draw the running header and the edge tab on every page (info: (header text, section key) per
    page); footer on the figure pages. Duplex: tabs on the outer edge (right on odd pages)."""
    import pymupdf
    doc = pymupdf.open(str(pdf_path))
    reg, bold = r"C:\Windows\Fonts\calibri.ttf", r"C:\Windows\Fonts\calibrib.ttf"
    W, H = pymupdf.paper_size("a4")
    mm = 72 / 25.4
    top, bottom, tab_w = 18 * mm, H - 14 * mm, 7 * mm
    step = (bottom - top) / max(len(groups), 1)
    gindex = {k: gi for gi, (_, ks) in enumerate(groups) for k in ks}
    for pi, page in enumerate(doc):
        txt, key = info[pi] if pi < len(info) else ("", "")
        page.insert_font(fontname="cal", fontfile=reg)
        page.insert_font(fontname="calb", fontfile=bold)
        if txt:
            page.insert_text((14 * mm, 9 * mm), txt[:130], fontname="cal", fontsize=7.5, color=(0.3, 0.3, 0.3))
        keys = key if isinstance(key, list) else [key]
        tabs = sorted({gindex[k] for k in keys if k in gindex})       # every group with content on this page
        right = (not duplex) or pi % 2 == 0                          # page 1 is a right-hand (odd) page
        x0 = W - tab_w if right else 0
        for gi in tabs:
            r = pymupdf.Rect(x0, top + gi * step, x0 + tab_w, top + (gi + 1) * step - 1.5)
            page.draw_rect(r, color=None, fill=PALETTE[gi % len(PALETTE)])
            page.insert_textbox(r + (0, 2, 0, -2), groups[gi][0], fontname="calb", fontsize=7, color=(1, 1, 1),
                                align=pymupdf.TEXT_ALIGN_CENTER, rotate=270 if right else 90)
        if fig_range and fig_range[0] <= pi + 1 <= fig_range[1]:
            page.insert_text((W / 2 - 40, H - 8 * mm), f"{title}    {pi + 1}", fontname="cal", fontsize=7,
                             color=(0.2, 0.2, 0.2))
    doc.save(str(out), garbage=3, deflate=True)
    doc.close()
