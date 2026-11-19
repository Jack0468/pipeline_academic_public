"""Rebuild annotated PDFs from note-app SVG exports whose slides render blank (zero AI tokens).

Some note apps export an annotated PDF as one tall SVG: the original PDF is embedded once per page
as `<image href="data:image/png;base64,JVBERi0…">` (a whole PDF mislabelled as PNG) with the ink as
`<path>`s on top. Browsers, Inkscape and pdf24 can't draw a PDF inside an <image>, so every
conversion keeps the ink and loses the slides.

This script puts them back together:
  1. finds each embedded PDF; the k-th <image> carrying a given PDF is page k of it;
  2. renders the ink alone (images and the paper background before them removed) with Inkscape;
  3. writes one output page per slide: the full canvas width (margin notes kept), from the slide's
     top edge to the next slide's, with the real PDF page underneath and the ink on top, at the
     slide's native size. Both layers stay vector.

Input:  `sources.annotated_svg` globs in the unit config.
Output: state/annotated/<unit id>/<name>_annotated.pdf, where <name> is the embedded PDF's file
        name when it is also in the unit's OneDrive folder, else the SVG's. List that folder under
        `sources.handwritten` and extract_handwriting.py transcribes the ink like a OneNote export.
Cached by SVG hash (state/manifest_annotated.json), so refresh.ps1 runs it for free.

Usage:
    python scripts/svg_notes_to_pdf.py [--unit ABCD1001] [--force]
"""
from __future__ import annotations

import argparse
import base64
import hashlib
import io
import re
import shutil
import subprocess
import sys
import tempfile
import xml.etree.ElementTree as ET
from pathlib import Path

from pypdf import PdfReader, PdfWriter, Transformation
from pypdf.generic import ArrayObject, DecodedStreamObject, DictionaryObject, FloatObject, NameObject

from common import ALLOW_SAT_HELP, work_units
from common import STATE, Manifest, file_hash, find_sources, load_units, unit_dir

SVG_NS, XLINK = "http://www.w3.org/2000/svg", "http://www.w3.org/1999/xlink"
ET.register_namespace("", SVG_NS)
ET.register_namespace("xlink", XLINK)
KEEP = {"defs", "style", "metadata", "title", "desc", "namedview", "clipPath", "mask", "symbol",
        "linearGradient", "radialGradient", "pattern", "marker", "filter"}   # never "background"
INKSCAPE = [r"C:\Program Files\Inkscape\bin\inkscape.com", "inkscape"]


# --- SVG geometry -------------------------------------------------------------------------------
def _num(s: str | None) -> float:
    return float(re.sub(r"[a-z%]+$", "", s or "0"))


def _mul(a, b):  # affine (a b c d e f) as in SVG matrix(); returns a∘b
    return (a[0] * b[0] + a[2] * b[1], a[1] * b[0] + a[3] * b[1],
            a[0] * b[2] + a[2] * b[3], a[1] * b[2] + a[3] * b[3],
            a[0] * b[4] + a[2] * b[5] + a[4], a[1] * b[4] + a[3] * b[5] + a[5])


def _parse_transform(s: str | None):
    m = (1.0, 0.0, 0.0, 1.0, 0.0, 0.0)
    for name, args in re.findall(r"(\w+)\s*\(([^)]*)\)", s or ""):
        v = [float(x) for x in re.split(r"[\s,]+", args.strip()) if x]
        if name == "matrix":
            t = tuple(v)
        elif name == "translate":
            t = (1, 0, 0, 1, v[0], v[1] if len(v) > 1 else 0)
        elif name == "scale":
            t = (v[0], 0, 0, v[1] if len(v) > 1 else v[0], 0, 0)
        else:
            raise ValueError(f"unsupported SVG transform {name}()")
        m = _mul(m, t)
    return m


def _image_pdf(el) -> bytes | None:
    href = el.get(f"{{{XLINK}}}href") or el.get("href") or ""
    if not href.startswith("data:") or "," not in href:
        return None
    raw = base64.b64decode(href.split(",", 1)[1])
    return raw if raw.startswith(b"%PDF") else None


def analyse(svg: Path):
    """Embedded-PDF pages in paint order, the ink-only SVG tree, and the canvas size (user units)."""
    tree = ET.parse(svg)
    root = tree.getroot()
    vb = [float(x) for x in re.split(r"[\s,]+", root.get("viewBox", "").strip()) if x]
    W, H = (vb[2], vb[3]) if len(vb) == 4 else (_num(root.get("width")), _num(root.get("height")))
    ox, oy = (vb[0], vb[1]) if len(vb) == 4 else (0.0, 0.0)

    pdfs: dict[str, bytes] = {}
    pages, seen = [], {}          # pages: dict(pdf=sha, index, x, y, w, h) in canvas units
    order = []                    # (element, parent) in document order
    def walk(el, ctm, parent):
        ctm = _mul(ctm, _parse_transform(el.get("transform")))
        order.append((el, parent))
        if el.tag == f"{{{SVG_NS}}}image" and (raw := _image_pdf(el)):
            if ctm[1] or ctm[2] or ctm[0] <= 0 or ctm[3] <= 0:
                raise ValueError(f"{svg.name}: rotated/flipped slide images are not supported")
            sha = hashlib.sha256(raw).hexdigest()[:16]
            pdfs.setdefault(sha, raw)
            x, y = _num(el.get("x")), _num(el.get("y"))
            pages.append(dict(pdf=sha, index=seen.get(sha, 0), el=el, par=(el.get("preserveAspectRatio") or ""),
                              x=ctm[0] * x + ctm[4] - ox, y=ctm[3] * y + ctm[5] - oy,
                              w=ctm[0] * _num(el.get("width")), h=ctm[3] * _num(el.get("height"))))
            seen[sha] = seen.get(sha, 0) + 1
        for c in list(el):
            walk(c, ctm, el)
    walk(root, (1, 0, 0, 1, 0, 0), None)
    if not pages:
        raise ValueError(f"{svg.name}: no embedded PDF pages (nothing to rebuild)")

    # Ink layer: drop the slide images, and everything painted before the first one (paper colour
    # and texture; it would cover the slides once the ink is laid on top).
    first = next(i for i, (el, _) in enumerate(order) if el is pages[0]["el"])
    ancestors = set()
    node = pages[0]["el"]
    parents = {id(el): p for el, p in order}
    while node is not None:
        ancestors.add(id(node))
        node = parents[id(node)]
    dropped = 0
    for el, par in order[:first]:
        if el.tag.split("}")[-1] in KEEP or par is None or id(el) in ancestors:
            continue
        if el in list(par):
            par.remove(el)
            dropped += 1
    for p in pages:
        parents[id(p["el"])].remove(p["el"])
        del p["el"]
    return pdfs, pages, tree, (W, H), dropped


# --- PDF assembly -------------------------------------------------------------------------------
def render_ink(tree: ET.ElementTree, workdir: Path) -> Path:
    src, dst = workdir / "ink.svg", workdir / "ink.pdf"
    tree.write(src, encoding="utf-8", xml_declaration=True)
    exe = next((e for e in INKSCAPE if Path(e).exists() or shutil.which(e)), None)
    if not exe:
        raise RuntimeError("Inkscape not found (install it, or edit INKSCAPE in this script)")
    r = subprocess.run([exe, str(src), "--export-type=pdf", f"--export-filename={dst}"],
                       capture_output=True, text=True)
    if not dst.exists():
        raise RuntimeError(f"Inkscape failed: {r.stderr.strip()[-400:]}")
    return dst


def ink_xobject(writer: PdfWriter, ink_pdf: Path):
    """The whole ink page as one Form XObject, shared by every output page (stored once)."""
    page = PdfReader(str(ink_pdf)).pages[0]
    box = page.mediabox
    form = DecodedStreamObject()
    form.set_data(page.get_contents().get_data())
    form.update({
        NameObject("/Type"): NameObject("/XObject"),
        NameObject("/Subtype"): NameObject("/Form"),
        NameObject("/BBox"): ArrayObject([FloatObject(v) for v in (box.left, box.bottom, box.right, box.top)]),
        NameObject("/Resources"): page["/Resources"].clone(writer) if "/Resources" in page else DictionaryObject(),
    })
    return writer._add_object(form), float(box.left), float(box.bottom), float(box.width), float(box.height)


def build(svg: Path, outdir: Path, unit_root: Path) -> tuple[Path, str]:
    pdfs, pages, tree, (W, H), dropped = analyse(svg)
    out = outdir / f"{output_name(svg, pdfs, unit_root)}_annotated.pdf"
    readers = {sha: PdfReader(io.BytesIO(raw)) for sha, raw in pdfs.items()}
    for sha, rd in readers.items():
        used = sum(p["pdf"] == sha for p in pages)
        if used > len(rd.pages):
            raise ValueError(f"{svg.name}: {used} images of a {len(rd.pages)}-page PDF")
    pages.sort(key=lambda p: (p["y"], p["x"]))

    # pt per canvas unit: the first slide's native width over its width on the canvas.
    first = readers[pages[0]["pdf"]].pages[pages[0]["index"]]
    k = float(first.cropbox.width) / pages[0]["w"]

    writer = PdfWriter()
    with tempfile.TemporaryDirectory(prefix="pa_svg_") as tmp:
        ink_ref, ix0, iy0, iw, ih = ink_xobject(writer, render_ink(tree, Path(tmp)))
    sx, sy = iw / W, ih / H                                   # Inkscape pt per canvas unit

    for i, p in enumerate(pages):
        top = 0.0 if i == 0 else p["y"]
        bottom = pages[i + 1]["y"] if i + 1 < len(pages) else H
        bw, bh = W * k, (bottom - top) * k
        out_page = writer.add_blank_page(width=bw, height=bh)

        # Slide: fit its crop box into the image rect (SVG default preserveAspectRatio: xMidYMid meet).
        slide = readers[p["pdf"]].pages[p["index"]]
        if slide.get("/Rotate", 0) % 360:
            slide.transfer_rotation_to_content()
        cb = slide.cropbox
        cw, ch = float(cb.width), float(cb.height)
        s = (p["w"] * k / cw, p["h"] * k / ch)
        s = s if p["par"].startswith("none") else (min(s),) * 2
        dx = p["x"] * k + (p["w"] * k - cw * s[0]) / 2
        dy_top = (p["y"] - top) * k + (p["h"] * k - ch * s[1]) / 2
        t = (Transformation().translate(-float(cb.left), -float(cb.bottom)).scale(s[0], s[1])
             .translate(dx, bh - dy_top - ch * s[1]))
        out_page.merge_transformed_page(slide, t)

        # Ink on top: canvas point (u, v) sits at Inkscape (ix0 + u*sx, iy0 + (H - v)*sy).
        a, d = k / sx, k / sy
        e, f = -ix0 * a, -iy0 * d - (H - bottom) * k
        res = out_page.setdefault(NameObject("/Resources"), DictionaryObject())
        xo = res.setdefault(NameObject("/XObject"), DictionaryObject())
        xo[NameObject("/Ink")] = ink_ref
        ops = f"q {a:.6f} 0 0 {d:.6f} {e:.4f} {f:.4f} cm /Ink Do Q".encode()
        contents = out_page.get_contents()
        cs = DecodedStreamObject()
        cs.set_data((contents.get_data() if contents is not None else b"") + b"\n" + ops)   # (empty dict ⇒ falsy)
        out_page.replace_contents(cs)

    writer.compress_identical_objects()
    for pg in writer.pages:
        pg.compress_content_streams()
    out.parent.mkdir(parents=True, exist_ok=True)
    with open(out, "wb") as fh:
        writer.write(fh)
    return out, (f"{len(pages)} pages from {len(pdfs)} embedded PDF(s), "
                 f"{dropped} background element(s) dropped, {out.stat().st_size / 1e6:.1f} MB")


def output_name(svg: Path, pdfs: dict[str, bytes], unit_root: Path) -> str:
    """Name after the embedded PDF when it also sits in the unit folder (e.g. Lecture_13)."""
    if len(pdfs) == 1:
        sha, raw = next(iter(pdfs.items()))
        for cand in unit_root.rglob("*.pdf"):
            try:
                if cand.stat().st_size == len(raw) and hashlib.sha256(cand.read_bytes()).hexdigest()[:16] == sha:
                    return re.sub(r"[^\w-]+", "_", cand.stem).strip("_")
            except OSError:
                continue
    return re.sub(r"[^\w-]+", "_", svg.stem).strip("_")


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("--allow-sat", action="store_true", help=ALLOW_SAT_HELP)
    ap.add_argument("--unit", nargs="*")
    ap.add_argument("--force", action="store_true")
    args = ap.parse_args()

    manifest = Manifest("annotated")
    for u in work_units(args.unit, args.allow_sat):
        svgs = find_sources(u, "annotated_svg")
        if not svgs:
            continue
        outdir = STATE / "annotated" / u["id"]
        for svg in svgs:
            h, key = file_hash(svg), str(svg)
            prev = manifest.data.get(key, {}).get("out")
            if not args.force and prev and Path(prev).exists() and manifest.done(key, h):
                print(f"{u['code']}: = {svg.name} (cached → {Path(prev).name})")
                continue
            try:
                out, msg = build(svg, outdir, unit_dir(u))
            except Exception as e:
                print(f"{u['code']}: FAIL {svg.name}: {e}")
                continue
            manifest.mark(key, h, out=str(out))
            print(f"{u['code']}: ok {svg.name} → {out.name} ({msg})")


if __name__ == "__main__":
    main()
