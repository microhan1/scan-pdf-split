"""Two-page spread splitting for scan-pdf-split.

A book scanned open puts two pages on one sheet. Each such spread becomes two
pages here, and nothing is re-rendered: the source is copied into a new PDF in
one pass, every spread is duplicated with ``fullcopy_page`` (the copy shares
the page's images and fonts), and each of the two gets a MediaBox/CropBox
covering one half. Scan quality is untouched and the file barely grows.

GUI and CLI share everything in this module.
"""
from __future__ import annotations

import logging
import math
import os
import re
import threading
import time
from dataclasses import dataclass, field
from typing import Callable, Optional

import numpy as np
import pymupdf
from PIL import Image

log = logging.getLogger("scan_pdf_split")

DIRECTIONS = ("ltr", "rtl")          # which half comes first
SELECTS = ("auto", "all", "none")    # which pages are split by default
PAGE_MODES = ("split", "whole", "skip")
POSITION_RANGE = (10.0, 90.0)        # split line, percent of the page width
OVERLAP_RANGE = (-4.0, 4.0)          # + both halves keep a strip of the gutter, - the strip is cut away
DEFAULT_POSITION = 50.0
LANDSCAPE_RATIO = 1.1                # wider than tall by this much = a spread
LARGE_PAGE_COUNT = 500
OUTPUT_SUFFIX = "_split"
PRODUCER = "scan-pdf-split (PyMuPDF)"

# Gutter detection works on a gray render this many pixels wide, whatever the
# page size, so a poster costs no more memory than a paperback.
DETECT_WIDTH = 600
DETECT_MAX_PIXELS = 2_000_000
DETECT_SEARCH = (0.3, 0.7)           # the gutter is looked for in this band of the width
# Band edges: running heads and folios at the top and bottom are left out.
_DETECT_TOP, _DETECT_BOTTOM = 0.08, 0.92
_SHADOW_DEPTH = 20.0                 # gray levels a gutter shadow must sit under its surroundings
_NARROW = 0.1                        # a shadow or valley wider than this share of the band is not a fold
_BLANK_RUN_MAX = 0.6                 # a "blank margin" wider than this is a blank page, not a gutter
# Measured on 960 held-out synthetic spreads with a known fold (fold 44-56%,
# gutter shadow none/faint/deep, noise, a figure across the middle, 0.6 deg
# tilt, narrow/wide inner margin, justified/ragged text): split line within 1%
# of the page width on 93% of them (a fixed 50% line: 20%), worst 1.3%. With
# any gutter shadow: 100% within 0.2%. Justified text without shadow: 100%
# within 0.2%. Ragged text on a flat scan is the weak spot (87% within 1%).


class Cancelled(Exception):
    """Raised inside process_pdf when the cancel event is set."""


class PasswordRequired(Exception):
    """The PDF is encrypted and no (or a wrong) password was given."""


class EmptyDocument(Exception):
    """The PDF has no pages, so there is nothing to split."""


class EmptyResult(Exception):
    """Every page was set to be left out, so there is nothing to save."""


@dataclass
class Options:
    position: float = DEFAULT_POSITION   # percent of the page width
    overlap: float = 0.0                 # percent of the page width, per side
    direction: str = "ltr"
    select: str = "auto"
    auto_detect: bool = True

    def validated(self) -> "Options":
        self.position = _clamped(self.position, POSITION_RANGE, DEFAULT_POSITION)
        self.overlap = _clamped(self.overlap, OVERLAP_RANGE, 0.0)
        if self.direction not in DIRECTIONS:
            self.direction = "ltr"
        if self.select not in SELECTS:
            self.select = "auto"
        self.auto_detect = bool(self.auto_detect)
        return self


def _clamped(value, bounds: tuple[float, float], default: float) -> float:
    try:
        v = float(value)
    except (TypeError, ValueError):
        return default
    if not math.isfinite(v) or isinstance(value, bool):
        return default
    return round(min(bounds[1], max(bounds[0], v)), 1)


@dataclass
class PagePlan:
    mode: str                  # "split", "whole" or "skip"
    position: float            # split line as a fraction of the visible width
    detected: bool = False     # position came from gutter detection
    failed: bool = False       # planning failed; the page is kept whole


@dataclass
class FileResult:
    input_path: str
    output_path: str
    pages: int                 # pages in the input
    out_pages: int             # pages written
    split_pages: int           # spreads that were split
    detected_pages: int        # spreads whose split line was found automatically
    failed_pages: list[int] = field(default_factory=list)
    elapsed: float = 0.0


# ---------------------------------------------------------------- PDF access

def open_pdf(path: str, password: Optional[str] = None) -> pymupdf.Document:
    """Open a PDF read-only. Raises PasswordRequired if it is encrypted and
    the password is missing or wrong. Never writes to ``path``."""
    doc = pymupdf.open(path)
    if doc.needs_pass:
        if not password or not doc.authenticate(password):
            doc.close()
            raise PasswordRequired(path)
    return doc


def _full_page_images(page: pymupdf.Page):
    """Image placements covering at least 40% of the page: the scan itself."""
    area = max(page.rect.width * page.rect.height, 1.0)
    try:
        infos = page.get_image_info()
    except Exception:
        return
    for info in infos:
        x0, y0, x1, y1 = info["bbox"]
        if x1 > x0 and (x1 - x0) * (y1 - y0) >= 0.4 * area:
            yield info


def is_scanned_pdf(doc: pymupdf.Document, sample: int = 5) -> bool:
    """True when the first pages are dominated by full-page images.
    A text-only PDF (no big image, but extractable text) returns False."""
    n = min(len(doc), sample)
    if n == 0:
        return True
    image_pages = text_pages = 0
    for i in range(n):
        page = doc[i]
        if any(True for _ in _full_page_images(page)):
            image_pages += 1
        elif len(page.get_text("text").strip()) > 20:
            text_pages += 1
    return text_pages <= image_pages


def inspect_pdf(path: str, password: Optional[str] = None) -> tuple[int, bool]:
    """(page count, is a scan) for a file about to be queued. Raises
    PasswordRequired, EmptyDocument, or whatever opening a broken file raises;
    each front end turns those into its own prompts and messages."""
    doc = open_pdf(path, password)
    try:
        pages = len(doc)
        if pages == 0:
            raise EmptyDocument(path)
        return pages, is_scanned_pdf(doc)
    finally:
        doc.close()


def output_path_for(input_path: str) -> str:
    """<name>_split.pdf next to the input; numbered if that name is taken by
    anything at all (a file, or a folder someone happened to name so)."""
    base, _ = os.path.splitext(input_path)
    candidate = f"{base}{OUTPUT_SUFFIX}.pdf"
    n = 2
    while os.path.exists(candidate):
        candidate = f"{base}{OUTPUT_SUFFIX}({n}).pdf"
        n += 1
    return candidate


def collect_pdfs(paths: list[str]) -> list[str]:
    """Expand folders (recursively) into PDF paths; keep order, drop dupes."""
    seen: set[str] = set()
    result: list[str] = []

    def add(p: str) -> None:
        key = os.path.normcase(os.path.abspath(p))
        if key not in seen:
            seen.add(key)
            result.append(os.path.abspath(p))

    for p in paths:
        if os.path.isdir(p):
            for root, _dirs, files in os.walk(p):
                for name in sorted(files):
                    if name.lower().endswith(".pdf"):
                        add(os.path.join(root, name))
        elif p.lower().endswith(".pdf") and os.path.isfile(p):
            add(p)
    return result


def human_size(num: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if num < 1024 or unit == "GB":
            return f"{num:.0f} {unit}" if unit in ("B", "KB") else f"{num:.1f} {unit}"
        num /= 1024.0
    return f"{num:.1f} GB"


# ------------------------------------------------------------------ planning

def is_spread(page: pymupdf.Page) -> bool:
    """A page wider than tall, as shown on screen (its /Rotate applied)."""
    r = page.rect
    return r.height > 0 and r.width / r.height > LANDSCAPE_RATIO


def default_mode(page: pymupdf.Page, opts: Options) -> str:
    if opts.select == "all":
        return "split"
    if opts.select == "none":
        return "whole"
    return "split" if is_spread(page) else "whole"


def plan_page(page: pymupdf.Page, opts: Options, override: Optional[dict] = None) -> PagePlan:
    """What happens to one page. ``override`` may carry a ``mode`` from
    PAGE_MODES and a ``position`` in percent, set by hand in the GUI."""
    override = override if isinstance(override, dict) else {}
    mode = override.get("mode")
    if mode not in PAGE_MODES:
        mode = default_mode(page, opts)
    position = opts.position / 100.0
    if mode != "split":
        return PagePlan(mode, position)
    manual = override.get("position")
    if manual is not None:
        return PagePlan(mode, _clamped(manual, POSITION_RANGE, opts.position) / 100.0)
    if opts.auto_detect:
        found = detect_gutter(page)
        if found is not None:
            return PagePlan(mode, found, detected=True)
    return PagePlan(mode, position)


# --------------------------------------------------------- gutter detection

def render_gray(page: pymupdf.Page, width: int = DETECT_WIDTH,
                max_pixels: int = DETECT_MAX_PIXELS) -> Optional[np.ndarray]:
    """The page as shown on screen, gray, about ``width`` pixels wide."""
    r = page.rect
    if r.width <= 0 or r.height <= 0:
        return None
    zoom = width / r.width
    zoom = min(zoom, math.sqrt(max_pixels / (r.width * r.height)))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csGRAY, alpha=False)
    if pix.width < 2 or pix.height < 2:
        return None
    arr = np.frombuffer(pix.samples, np.uint8).reshape(pix.height, pix.stride)
    return arr[:, :pix.width]


def _smooth(a: np.ndarray, r: int) -> np.ndarray:
    """Moving average over 2r+1 columns; the window shrinks at the ends."""
    c = np.concatenate(([0.0], np.cumsum(a, dtype=np.float64)))
    idx = np.arange(len(a))
    lo = np.maximum(0, idx - r)
    hi = np.minimum(len(a) - 1, idx + r)
    return (c[hi + 1] - c[lo]) / (hi - lo + 1)


def _round_half_up(x: float) -> int:
    return int(math.floor(x + 0.5))


def find_gutter(gray: np.ndarray) -> Optional[float]:
    """Split line for a spread, as a fraction of its width, or None when the
    page gives no clear answer (blank, a photo across the middle, ...).

    Two cues, in order:
    1. A narrow valley of darkness: the gutter shadow. Its floor is the fold.
    2. Otherwise columns that are uniform top to bottom (no text) near the
       middle: the blank inner margins. The fold is their middle.
    """
    h, w = gray.shape
    x0, x1 = int(math.floor(w * DETECT_SEARCH[0])), int(math.ceil(w * DETECT_SEARCH[1]))
    y0, y1 = int(math.floor(h * _DETECT_TOP)), int(math.ceil(h * _DETECT_BOTTOM))
    if x1 - x0 < 10 or y1 - y0 < 5:
        return None
    band = gray[y0:y1, x0:x1].astype(np.float64)
    ms = _smooth(band.mean(axis=0), 2)
    sds = _smooth(band.std(axis=0), 3)
    n = len(ms)
    sorted_sd = np.sort(sds)
    lo, q = float(sorted_sd[0]), float(sorted_sd[int(n * 0.6)])
    if q - lo < 3 and float(ms.max() - ms.min()) < 12:
        return None                                  # nothing to go on (blank, flat)

    # 1) shadow valley, measured against its own surroundings so that two
    #    halves of different brightness do not fake or hide it
    dark = int(np.argmin(ms))
    win, core = _round_half_up(n * 0.1), max(1, _round_half_up(n * 0.02))
    around = [float(ms[k]) for k in range(max(0, dark - win), min(n - 1, dark + win) + 1) if abs(k - dark) > core]
    around.sort()
    base = around[len(around) // 2] if around else float(ms[dark])
    depth = base - float(ms[dark])
    if depth > _SHADOW_DEPTH:
        half = base - depth * 0.5
        a = b = dark
        while a > 0 and ms[a - 1] < half:
            a -= 1
        while b < n - 1 and ms[b + 1] < half:
            b += 1
        if b - a < n * _NARROW and a > 0 and b < n - 1:
            return _to_fraction(x0 + dark, w)

    # 2) the blank-margin run closest to (and preferably around) the middle
    thr = lo + 0.35 * (q - lo)
    center = w * 0.5 - x0
    best = None
    k = 0
    while k < n:
        if sds[k] > thr:
            k += 1
            continue
        e = k
        while e + 1 < n and sds[e + 1] <= thr:
            e += 1
        dist = max(0.0, abs((k + e) / 2 - center) - (e - k) / 2)
        score = dist - min(e - k, n * 0.25) * 0.25
        if best is None or score < best[2]:
            best = (k, e, score)
        k = e + 1
    if best is None:
        return None
    a0, b0, _ = best
    seg = ms[a0:b0 + 1]
    dark = a0 + int(np.argmin(seg))
    med = float(np.sort(seg)[len(seg) // 2])
    x = (a0 + b0) / 2
    if med - float(ms[dark]) > 10:                   # a faint shadow inside the margin
        half = med - (med - float(ms[dark])) * 0.5
        a = b = dark
        while a > a0 and ms[a - 1] < half:
            a -= 1
        while b < b0 and ms[b + 1] < half:
            b += 1
        # Only a valley with paper on both sides is a shadow. At the run's
        # ends the smoothed mean already dips toward the text column next to
        # it; taking that dip put the line at the margin's edge on flat scans
        # (flat spreads within 1% of the fold: 37% -> 80%, worst 5.8% -> 1.3%).
        if b - a < n * _NARROW and a > a0 and b < b0:
            x = dark
    # a run touching the band's edge or far too wide is a blank page or a
    # photo, not a margin: fall back to the middle rather than guess
    if a0 == 0 or b0 == n - 1 or b0 - a0 > n * _BLANK_RUN_MAX:
        x = center
    return _to_fraction(x0 + x, w)


def _to_fraction(x: float, w: int) -> float:
    return round(min(DETECT_SEARCH[1], max(DETECT_SEARCH[0], x / w)), 3)


def detect_gutter(page: pymupdf.Page) -> Optional[float]:
    gray = render_gray(page)
    return None if gray is None else find_gutter(gray)


# ------------------------------------------------------------ page geometry

_NUM = re.compile(r"[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")


def _read_box(doc: pymupdf.Document, xref: int, key: str) -> Optional[tuple[float, float, float, float]]:
    """A page box straight from the PDF object, normalized to (x0, y0, x1, y1)
    with x0 < x1 and y0 < y1 in PDF user space (origin bottom left)."""
    kind, value = doc.xref_get_key(xref, key)
    if kind == "xref":                               # indirect array
        value = doc.xref_object(int(value.split()[0]), compressed=True)
        kind = "array" if value.lstrip().startswith("[") else kind
    if kind != "array":
        return None
    nums = [float(v) for v in _NUM.findall(value)]
    if len(nums) != 4:
        return None
    x0, y0, x1, y1 = nums
    return min(x0, x1), min(y0, y1), max(x0, x1), max(y0, y1)


def visible_box(doc: pymupdf.Document, xref: int) -> tuple[float, float, float, float]:
    """What a viewer shows: the CropBox clipped to the MediaBox."""
    media = _read_box(doc, xref, "MediaBox")
    if media is None:
        raise ValueError("page has no MediaBox")
    crop = _read_box(doc, xref, "CropBox") or media
    box = (max(crop[0], media[0]), max(crop[1], media[1]), min(crop[2], media[2]), min(crop[3], media[3]))
    if box[2] - box[0] <= 0 or box[3] - box[1] <= 0:
        raise ValueError("page box is empty")
    return box


def halves(position: float, overlap: float) -> tuple[tuple[float, float], tuple[float, float]]:
    """(left, right) spans of the visible width, as fractions. ``overlap`` is
    a fraction of the width added to (or, if negative, cut from) each side."""
    left = (0.0, min(1.0, max(0.0, position + overlap)))
    right = (min(1.0, max(0.0, position - overlap)), 1.0)
    return left, right


def visual_span_to_box(box: tuple[float, float, float, float], rotation: int,
                       span: tuple[float, float]) -> tuple[float, float, float, float]:
    """The part of ``box`` that shows as the horizontal ``span`` of the page
    on screen, once /Rotate (clockwise) is applied."""
    x0, y0, x1, y1 = box
    w, h = x1 - x0, y1 - y0
    a, b = span
    if rotation == 90:        # screen left..right runs along +y
        return x0, y0 + h * a, x1, y0 + h * b
    if rotation == 180:       # along -x
        return x0 + w * (1 - b), y0, x0 + w * (1 - a), y1
    if rotation == 270:       # along -y
        return x0, y0 + h * (1 - b), x1, y0 + h * (1 - a)
    return x0 + w * a, y0, x0 + w * b, y1


def _fmt(v: float) -> str:
    s = f"{v:.4f}".rstrip("0").rstrip(".")
    return "0" if s in ("-0", "") else s


def _set_box(doc: pymupdf.Document, xref: int, box: tuple[float, float, float, float]) -> None:
    arr = "[" + " ".join(_fmt(v) for v in box) + "]"
    doc.xref_set_key(xref, "MediaBox", arr)
    doc.xref_set_key(xref, "CropBox", arr)
    for key in ("TrimBox", "BleedBox", "ArtBox"):
        doc.xref_set_key(xref, key, "null")


def _keep_annots(doc: pymupdf.Document, xref: int, box: tuple[float, float, float, float]) -> None:
    """Drop the annotations and links of a half page that lie outside it, so
    a link on the left page does not also fire on the right one. An
    annotation straddling the split stays on both halves."""
    kind, value = doc.xref_get_key(xref, "Annots")
    if kind == "null":
        return
    if kind == "xref":
        value = doc.xref_object(int(value.split()[0]), compressed=True)
    refs = [int(r) for r in re.findall(r"(\d+)\s+0\s+R", value)]
    keep = []
    for ref in refs:
        rect = _read_box(doc, ref, "Rect")
        if rect is None or (rect[0] < box[2] and rect[2] > box[0] and rect[1] < box[3] and rect[3] > box[1]):
            keep.append(ref)
            # /P is optional but form fields are looked up through it, and a
            # copied page's annotations would otherwise point at nothing
            doc.xref_set_key(ref, "P", f"{xref} 0 R")
    doc.xref_set_key(xref, "Annots", "[" + " ".join(f"{r} 0 R" for r in keep) + "]" if keep else "null")


def split_page_in_place(out: pymupdf.Document, index: int, position: float, overlap: float,
                        direction: str) -> None:
    """Turn page ``index`` of ``out`` into two consecutive pages. The copy
    shares its images and fonts with the original; only the boxes differ."""
    xref = out[index].xref
    box = visible_box(out, xref)            # fails before anything changes
    rotation = out[index].rotation % 360
    left, right = halves(position, overlap)
    first, second = (left, right) if direction == "ltr" else (right, left)
    saved = {k: out.xref_get_key(xref, k) for k in _PAGE_KEYS}
    out.fullcopy_page(index, index + 1 if index + 1 < out.page_count else -1)
    try:
        for i, span in ((index, first), (index + 1, second)):
            page_xref = out[i].xref
            part = visual_span_to_box(box, rotation, span)
            _set_box(out, page_xref, part)
            _keep_annots(out, page_xref, part)
    except Exception:
        # undo: drop the copy and give the page back its own boxes and links
        out.delete_page(index + 1)
        for key, (kind, value) in saved.items():
            out.xref_set_key(xref, key, "null" if kind == "null" else value)
        raise


_PAGE_KEYS = ("MediaBox", "CropBox", "TrimBox", "BleedBox", "ArtBox", "Annots")


# ------------------------------------------------------------- whole files

def page_map(plans: list[PagePlan]) -> list[Optional[int]]:
    """1-based first output page for every input page (None if left out)."""
    result, n = [], 1
    for p in plans:
        if p.mode == "skip":
            result.append(None)
        else:
            result.append(n)
            n += 2 if p.mode == "split" else 1
    return result


def _remap_toc(toc: list, first_out: list[Optional[int]]) -> list:
    """Bookmarks follow their page; one on a left-out page moves to the next
    page that is kept. Levels are kept consistent (no jump by more than one)."""
    result = []
    for entry in toc:
        level, title, page = entry[0], entry[1], entry[2]
        target = None
        if isinstance(page, int) and 1 <= page <= len(first_out):
            target = next((p for p in first_out[page - 1:] if p is not None), None)
        if target is None:
            continue
        prev = result[-1][0] if result else 0
        result.append([min(level, prev + 1), title, target])
    return result


def _copy_pages(out: pymupdf.Document, doc: pymupdf.Document, first: int, last: int,
                lost: dict[int, Exception]) -> None:
    """Copy pages first..last in as few passes as possible. One pass keeps
    images and fonts that several pages use as a single shared object; a
    damaged file can make that pass fail, so the range is halved until the
    bad pages are isolated. A page that cannot be copied at all becomes a
    blank page of the same size (a viewer shows it blank too) and is
    reported in ``lost``, so page numbers still line up with the input."""
    before = out.page_count
    try:
        out.insert_pdf(doc, from_page=first, to_page=last)
        return
    except Exception as exc:
        if out.page_count > before:                  # drop a half-done pass
            out.delete_pages(before, out.page_count - 1)
        if first == last:
            try:
                r = doc[first].rect
                out.new_page(width=max(r.width, 1), height=max(r.height, 1))
            except Exception:
                out.new_page()
            lost[first] = exc
            return
    mid = (first + last) // 2
    _copy_pages(out, doc, first, mid, lost)
    _copy_pages(out, doc, mid + 1, last, lost)


def _save_atomic(out: pymupdf.Document, path: str) -> None:
    """Write next to the target, then rename: an interrupted save leaves at
    most a stray .part file, never a truncated result under the real name."""
    folder, name = os.path.split(path)
    tmp = os.path.join(folder, f".{name}.{os.getpid()}.part")
    try:
        out.save(tmp, garbage=3, deflate=True)
        os.replace(tmp, path)
    finally:
        if os.path.exists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass


def plan_document(doc: pymupdf.Document, opts: Options, overrides: Optional[dict] = None,
                  progress: Optional[Callable[[int, int], None]] = None,
                  cancel: Optional[threading.Event] = None,
                  page_failed: Optional[Callable[[int, Exception], None]] = None) -> list[PagePlan]:
    """Plans for every page. A page whose plan cannot be made is kept whole
    and reported through ``page_failed``."""
    opts = opts.validated()
    overrides = overrides if isinstance(overrides, dict) else {}
    n = len(doc)
    plans: list[PagePlan] = []
    for i in range(n):
        if cancel is not None and cancel.is_set():
            raise Cancelled(doc.name)
        try:
            plans.append(plan_page(doc[i], opts, overrides.get(i)))
        except Exception as exc:
            log.warning("%s page %d could not be planned: %s", doc.name, i + 1, exc)
            if page_failed:
                page_failed(i + 1, exc)
            plans.append(PagePlan("whole", opts.position / 100.0, failed=True))
        if progress:
            progress(i + 1, n)
    return plans


def process_pdf(
    input_path: str,
    opts: Options,
    password: Optional[str] = None,
    output_path: Optional[str] = None,
    progress: Optional[Callable[[int, int], None]] = None,
    cancel: Optional[threading.Event] = None,
    page_failed: Optional[Callable[[int, Exception], None]] = None,
    overrides: Optional[dict] = None,
) -> FileResult:
    """Split one PDF into a new one. The input is never modified. A page that
    fails is kept whole, as in the original, and reported through
    ``page_failed``. Raises Cancelled (writing nothing) if ``cancel`` is set.
    ``overrides`` maps a 0-based page index to {"mode": ..., "position": %}."""
    opts = opts.validated()
    doc = open_pdf(input_path, password)
    out = None
    t0 = time.perf_counter()
    try:
        n = len(doc)
        if n == 0:
            raise EmptyDocument(input_path)
        failed: list[int] = []

        def failed_page(page: int, exc: Exception) -> None:
            if page not in failed:
                failed.append(page)
            if page_failed:
                page_failed(page, exc)

        plans = plan_document(doc, opts, overrides, progress, cancel, failed_page)
        if all(p.mode == "skip" for p in plans):
            raise EmptyResult(input_path)
        if cancel is not None and cancel.is_set():
            raise Cancelled(input_path)

        out = pymupdf.open()
        lost: dict[int, Exception] = {}
        _copy_pages(out, doc, 0, n - 1, lost)
        for i, exc in lost.items():
            log.warning("%s page %d could not be copied: %s", input_path, i + 1, exc)
            if plans[i].mode != "skip":
                plans[i].mode = "whole"      # it is a blank stand-in now; nothing to split
            failed_page(i + 1, exc)
        # Work from the back so that splitting or dropping a page never moves
        # the pages still to be handled.
        for i in range(n - 1, -1, -1):
            plan = plans[i]
            if plan.mode == "skip":
                out.delete_page(i)
            elif plan.mode == "split":
                try:
                    split_page_in_place(out, i, plan.position, opts.overlap / 100.0, opts.direction)
                except Exception as exc:
                    # split_page_in_place has already undone itself: the page
                    # is back to one whole page, as in the original
                    log.warning("%s page %d could not be split: %s", input_path, i + 1, exc)
                    plan.mode = "whole"
                    failed_page(i + 1, exc)
        if cancel is not None and cancel.is_set():
            raise Cancelled(input_path)
        _restore_metadata(doc, out, plans)
        out_path = output_path or output_path_for(input_path)
        if cancel is not None and cancel.is_set():
            raise Cancelled(input_path)
        _save_atomic(out, out_path)
        return FileResult(
            input_path, out_path, n, out.page_count,
            sum(1 for p in plans if p.mode == "split"),
            sum(1 for p in plans if p.mode == "split" and p.detected),
            sorted(failed), time.perf_counter() - t0,
        )
    finally:
        if out is not None:
            out.close()
        doc.close()


def _restore_metadata(doc: pymupdf.Document, out: pymupdf.Document, plans: list[PagePlan]) -> None:
    # set_metadata replaces every field, so producer has to be named here or
    # the result ends up with no producer at all
    meta = {k: v for k, v in (doc.metadata or {}).items()
            if k in ("title", "author", "subject", "keywords", "creator") and v}
    meta["producer"] = PRODUCER
    try:
        out.set_metadata(meta)
    except Exception as exc:
        log.info("metadata not copied: %s", exc)
    try:
        toc = doc.get_toc(simple=True)
    except Exception:
        toc = []
    if toc:
        try:
            out.set_toc(_remap_toc(toc, page_map(plans)))
        except Exception as exc:     # a malformed outline must not cost the file
            log.info("bookmarks not copied: %s", exc)


# ------------------------------------------------------------------ preview

def render_preview(doc: pymupdf.Document, index: int, max_size: tuple[int, int]) -> Image.Image:
    """Page ``index`` as shown on screen, fitted into ``max_size``."""
    page = doc[index]
    r = page.rect
    if r.width <= 0 or r.height <= 0:
        return Image.new("RGB", (1, 1), "white")
    zoom = min(max_size[0] / r.width, max_size[1] / r.height)
    zoom = min(zoom, math.sqrt(DETECT_MAX_PIXELS * 2 / (r.width * r.height)))
    pix = page.get_pixmap(matrix=pymupdf.Matrix(zoom, zoom), colorspace=pymupdf.csRGB, alpha=False)
    return Image.frombytes("RGB", (pix.width, pix.height), pix.samples)


def split_preview(image: Image.Image, plan: PagePlan, overlap: float, direction: str) -> list[Image.Image]:
    """What the page turns into, cut from its rendered image: two halves in
    output order for a split, the page itself when kept whole, nothing when
    left out."""
    if plan.mode == "skip":
        return []
    if plan.mode == "whole":
        return [image]
    left, right = halves(plan.position, overlap)
    w, h = image.size
    parts = [image.crop((int(round(a * w)), 0, max(int(round(a * w)) + 1, int(round(b * w))), h))
             for a, b in (left, right)]
    return parts if direction == "ltr" else parts[::-1]
