"""Real text editing inside PDF content streams.

Editing a line works like this:
  1. Extract characters (glyph origin + box + font) with MuPDF and group them
     into *visual* lines / paragraphs by geometry (not content-stream order, so
     text written by earlier edits merges back into its line).
  2. Diff old vs. new text; only characters from the first change onwards are
     touched, so the untouched prefix keeps its original glyphs byte-for-byte.
  3. Remove those glyphs from the content stream with a text-only redaction
     (images and vector graphics are left alone, nothing is painted over).
  4. Write the replacement at the same baseline with the best matching font
     (embedded font -> system font -> base-14 -> fallback), per character.
     The unchanged tail of the line is re-set at its original spacing, shifted
     by the width difference of the edit.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field

import pymupdf as fitz

from .fonts import INDEX, name_style, norm, strip_subset
from .geometry import Frame, dot, int_to_hex, int_to_rgb, rect_corners, unrotated

TEXT_FLAGS = fitz.TEXT_PRESERVE_WHITESPACE | fitz.TEXT_MEDIABOX_CLIP
RTL_RE = re.compile(r"[֐-ࣿיִ-﷿ﹰ-﻿]")
_SPACE_LIKE = str.maketrans({" ": " ", " ": " ", " ": " ", "\t": " "})


class EditError(Exception):
    pass


# ---------------------------------------------------------------------------
# fonts
# ---------------------------------------------------------------------------
@dataclass
class Style:
    chain: list[fitz.Font]
    size: float
    color: tuple[float, float, float]

    def runs(self, text: str) -> list[tuple[str, fitz.Font]]:
        """Split text into runs, each drawn with the first font that has the glyphs."""
        out: list[tuple[str, fitz.Font]] = []
        for ch in text:
            cp = ord(ch)
            font = next((f for f in self.chain if f.has_glyph(cp)), self.chain[0])
            if out and out[-1][1] is font:
                out[-1] = (out[-1][0] + ch, font)
            else:
                out.append((ch, font))
        return out

    def width(self, text: str) -> float:
        return sum(f.text_length(t, fontsize=self.size) for t, f in self.runs(text))

    @property
    def ascender(self) -> float:
        return (self.chain[0].ascender or 0.8) * self.size


def span_flags(span: dict) -> tuple[bool, bool, bool, bool]:
    nb, ni = name_style(span.get("font", ""))
    fl = span.get("flags", 0)
    return bool(fl & 16) or nb, bool(fl & 2) or ni, bool(fl & 4), bool(fl & 8)


class FontResolver:
    """Builds font fallback chains for one document (per request)."""

    def __init__(self, doc: fitz.Document):
        self.doc = doc
        self._embedded: dict[int, fitz.Font | None] = {}

    def _embedded_for(self, page: fitz.Page, fontname: str) -> fitz.Font | None:
        want = norm(strip_subset(fontname))
        if not want:
            return None
        for item in page.get_fonts(full=True):
            xref, ext, ftype, basefont = item[0], item[1], item[2], item[3]
            have = norm(strip_subset(basefont))
            if not have or ftype == "Type3" or ext in ("n/a", ""):
                continue
            if have == want or have.startswith(want) or want.startswith(have):
                if xref not in self._embedded:
                    font = None
                    try:
                        _n, _e, _t, buf = self.doc.extract_font(xref)
                        if buf:
                            font = fitz.Font(fontbuffer=buf)
                            # a font without a usable unicode cmap cannot be re-used
                            if not font.has_glyph(ord("e")) and not font.has_glyph(ord("1")):
                                font = None
                    except Exception:
                        font = None
                    self._embedded[xref] = font
                if self._embedded[xref] is not None:
                    return self._embedded[xref]
        return None

    def span_style(self, page: fitz.Page, span: dict, override: dict | None = None) -> Style:
        name = span.get("font", "")
        bold, italic, serif, mono = span_flags(span)
        ov = override or {}
        size = float(ov.get("size") or span.get("size") or 11)
        color = ov.get("color_rgb") or int_to_rgb(span.get("color", 0))
        want_bold = bold if ov.get("bold") is None else bool(ov["bold"])
        want_italic = italic if ov.get("italic") is None else bool(ov["italic"])
        keys: list[str] = []
        chain: list[fitz.Font] = []
        if ov.get("font"):
            keys.append(INDEX.resolve_user_font(ov["font"], want_bold, want_italic))
        else:
            if want_bold == bold and want_italic == italic:
                emb = self._embedded_for(page, name)
                if emb is not None:
                    chain.append(emb)
            sysf = INDEX.system_match(name, want_bold, want_italic)
            if sysf:
                keys.append(sysf)
            keys.append(INDEX.base14_for(name, want_bold, want_italic, serif, mono))
        keys += INDEX.fallbacks(want_bold, want_italic)
        for k in keys:
            f = INDEX.load(k)
            if f is not None and all(f is not c for c in chain):
                chain.append(f)
        return Style(chain=chain, size=size, color=color)

    @staticmethod
    def user_style(font_key: str, size: float, color, bold: bool, italic: bool) -> Style:
        keys = [INDEX.resolve_user_font(font_key or "base:helvetica", bold, italic)]
        keys += INDEX.fallbacks(bold, italic)
        chain = [f for f in (INDEX.load(k) for k in keys) if f is not None]
        return Style(chain=chain, size=size, color=color or (0, 0, 0))


# ---------------------------------------------------------------------------
# extraction -> visual lines & paragraphs
# ---------------------------------------------------------------------------
def raw_blocks(page: fitz.Page) -> list[dict]:
    blocks = page.get_text("rawdict", flags=TEXT_FLAGS)["blocks"]
    for b in blocks:  # treat no-break / figure spaces as plain spaces
        for l in b.get("lines", ()):
            for sp in l["spans"]:
                for c in sp["chars"]:
                    c["c"] = c["c"].translate(_SPACE_LIKE)
    return blocks


def _advance(ch: dict, d) -> float:
    o = ch["origin"]
    return max(dot((x - o[0], y - o[1]), d) for x, y in rect_corners(ch["bbox"]))


def dominant_span(spans: list[dict]) -> dict:
    return max(spans, key=lambda s: len("".join(c["c"] for c in s["chars"]).strip()) or 0.1)


@dataclass
class VLine:
    pairs: list[tuple[dict, dict]]          # (char, span); virtual spaces carry "virtual"
    dir: tuple[float, float]
    t0: float                                # extent along baseline (absolute)
    t1: float
    base: float                              # baseline position across the direction
    size: float
    bold: bool = False
    segs: list = field(default_factory=list)

    @property
    def text(self) -> str:
        return "".join(c["c"] for c, _ in self.pairs)

    @property
    def spans(self) -> list[dict]:
        seen, out = set(), []
        for _, sp in self.pairs:
            if id(sp) not in seen:
                seen.add(id(sp))
                out.append(sp)
        return out

    @property
    def bbox(self) -> fitz.Rect:
        r = fitz.EMPTY_RECT()
        for c, _ in self.pairs:
            if not c.get("virtual"):
                r |= fitz.Rect(c["bbox"])
        return r


def _seg_from_line(l: dict) -> dict | None:
    pairs = [(c, sp) for sp in l["spans"] for c in sp["chars"]]
    if not any(c["c"].strip() for c, _ in pairs):
        return None
    d = tuple(l["dir"])
    n = (-d[1], d[0])
    o = pairs[0][0]["origin"]
    dom = dominant_span(l["spans"])
    return {
        "pairs": pairs, "dir": d,
        "t0": dot(o, d),
        "t1": max(dot(c["origin"], d) + _advance(c, d) for c, _ in pairs),
        "base": dot(o, n), "size": dom.get("size") or 10,
        "bold": span_flags(dom)[0],
    }


def visual_lines(blocks: list[dict]) -> list[VLine]:
    segs = [s for b in blocks if b.get("type") == 0 for l in b["lines"] if (s := _seg_from_line(l))]
    segs.sort(key=lambda s: (round(s["dir"][0], 2), round(s["dir"][1], 2), round(s["base"], 1), s["t0"]))
    groups: list[list[dict]] = []
    for s in segs:
        for g in groups:
            h = g[0]
            if abs(h["dir"][0] - s["dir"][0]) > 0.01 or abs(h["dir"][1] - s["dir"][1]) > 0.01:
                continue
            sz = min(h["size"], s["size"])
            if abs(h["base"] - s["base"]) > 0.3 * sz:
                continue
            if any(s["t0"] - o["t1"] < 0.8 * sz and o["t0"] - s["t1"] < 0.8 * sz for o in g):
                g.append(s)
                break
        else:
            groups.append([s])

    out: list[VLine] = []
    for g in groups:
        g.sort(key=lambda s: s["t0"])
        d = g[0]["dir"]
        pairs: list[tuple[dict, dict]] = []
        for i, s in enumerate(g):
            if pairs:
                pc, psp = pairs[-1]
                gap = s["t0"] - g[i - 1]["t1"]
                if gap > 0.15 * s["size"] and pc["c"] != " " and s["pairs"][0][0]["c"] != " ":
                    adv = _advance(pc, d)
                    o = (pc["origin"][0] + d[0] * adv, pc["origin"][1] + d[1] * adv)
                    pairs.append(({"c": " ", "origin": o, "bbox": (o[0], o[1], o[0], o[1]),
                                   "virtual": True}, psp))
            pairs += s["pairs"]
        dom = max(g, key=lambda s: len(s["pairs"]))
        out.append(VLine(pairs=pairs, dir=d, t0=g[0]["t0"], t1=max(s["t1"] for s in g),
                         base=g[0]["base"], size=dom["size"], bold=dom["bold"], segs=g))
    out.sort(key=lambda v: (round(v.bbox.y0, 1), v.bbox.x0))
    return out


def paragraphs(vlines: list[VLine]) -> list[list[int]]:
    """Group visual lines into paragraphs (stacked lines, similar size and leading)."""
    order = sorted(range(len(vlines)), key=lambda i: (round(vlines[i].dir[0], 2),
                                                       round(vlines[i].dir[1], 2), vlines[i].base))
    paras: list[list[int]] = []
    for i in order:
        v = vlines[i]
        placed = False
        for p in reversed(paras):
            last = vlines[p[-1]]
            if abs(last.dir[0] - v.dir[0]) > 0.01 or abs(last.dir[1] - v.dir[1]) > 0.01:
                continue
            sz = max(last.size, v.size)
            step = v.base - last.base
            if not (0.75 * sz <= step <= 1.9 * sz):
                continue
            if not (0.85 <= v.size / last.size <= 1.18) or v.bold != last.bold:
                continue
            if min(v.t1, last.t1) - max(v.t0, last.t0) <= 0:
                continue
            if len(p) > 1:
                prev_step = last.base - vlines[p[-2]].base
                if abs(step - prev_step) > 0.3 * sz:
                    continue
            p.append(i)
            placed = True
            break
        if not placed:
            paras.append([i])
    paras.sort(key=lambda p: (round(vlines[p[0]].bbox.y0, 1), vlines[p[0]].bbox.x0))
    return paras


def para_text(vlines: list[VLine], idxs: list[int]) -> str:
    out = ""
    for i in idxs:
        t = vlines[i].text.strip()
        if out and not out.endswith("-"):
            out += " "
        out += t
    return out


def _span_info(span: dict) -> dict:
    bold, italic, serif, mono = span_flags(span)
    return {
        "font": strip_subset(span.get("font", "")),
        "size": round(span.get("size", 0), 2),
        "color": int_to_hex(span.get("color", 0)),
        "bold": bold, "italic": italic, "serif": serif, "mono": mono,
    }


def page_text_model(page: fitz.Page, fr: Frame) -> dict:
    """Lines and paragraphs of a page, in display coordinates, for the editor UI."""
    vl = visual_lines(raw_blocks(page))
    lines_out = []
    for i, v in enumerate(vl):
        dvec = fitz.Point(v.dir) * fr.to_disp - fitz.Point(0, 0) * fr.to_disp
        lines_out.append({
            "id": i,
            "bbox": fr.rect_disp(v.bbox),
            "text": v.text,
            "angle": round(math.degrees(math.atan2(dvec.y, dvec.x)), 1),
            **_span_info(dominant_span(v.spans)),
        })
    paras_out = []
    for pi, idxs in enumerate(paragraphs(vl)):
        r = fitz.EMPTY_RECT()
        for i in idxs:
            r |= vl[i].bbox
        paras_out.append({"id": pi, "bbox": fr.rect_disp(r), "text": para_text(vl, idxs), "lines": idxs,
                          **_span_info(dominant_span([s for i in idxs for s in vl[i].spans]))})
    return {"lines": lines_out, "paragraphs": paras_out}


# ---------------------------------------------------------------------------
# geometry of characters on a baseline
# ---------------------------------------------------------------------------
def char_kill_rect(ch: dict, span: dict, d) -> fitz.Rect:
    """Small rectangle that hits this glyph but not its neighbours (incl. other lines)."""
    size = span.get("size") or 10
    adv = _advance(ch, d)
    if adv <= 0.05:
        adv = size * 0.3
    up = (d[1], -d[0])
    o = ch["origin"]
    r = None
    for t in (0.15 * adv, 0.85 * adv):
        for h in (0.7 * size, -0.2 * size):
            p = fitz.Point(o[0] + d[0] * t + up[0] * h, o[1] + d[1] * t + up[1] * h)
            r = fitz.Rect(p, p) if r is None else r | p
    if r.width < 0.2:
        r.x0 -= 0.1
        r.x1 += 0.1
    if r.height < 0.2:
        r.y0 -= 0.1
        r.y1 += 0.1
    return r


@dataclass
class WriteOp:
    x: float  # local frame (baseline direction = +x, down = +y) relative to the plan origin
    y: float
    text: str
    style: Style


@dataclass
class Plan:
    redact: list[fitz.Rect] = field(default_factory=list)
    ops: list[WriteOp] = field(default_factory=list)
    origin: tuple[float, float] = (0, 0)
    angle: float = 0.0


def _local(p, origin, d) -> tuple[float, float]:
    v = (p[0] - origin[0], p[1] - origin[1])
    return dot(v, d), dot(v, (-d[1], d[0]))


def execute(page: fitz.Page, plans: list[Plan]) -> None:
    """Remove the glyphs of all plans in one redaction pass, then write new text."""
    plans = [p for p in plans if p.redact or p.ops]
    if not plans:
        return
    rects = [r for p in plans for r in p.redact]
    for r in rects:
        page.add_redact_annot(r, fill=False, cross_out=False)
    if rects:
        page.apply_redactions(images=fitz.PDF_REDACT_IMAGE_NONE,
                              graphics=fitz.PDF_REDACT_LINE_ART_NONE,
                              text=fitz.PDF_REDACT_TEXT_REMOVE)
    for plan in plans:
        write_ops(page, plan.ops, plan.origin, plan.angle)


def write_ops(page: fitz.Page, ops: list[WriteOp], origin, angle: float) -> None:
    writers: dict[tuple, fitz.TextWriter] = {}
    ox, oy = origin
    for op in ops:
        if not op.text:
            continue
        x = op.x
        key = tuple(round(c, 4) for c in op.style.color)
        tw = writers.setdefault(key, fitz.TextWriter(page.rect))
        for run, font in op.style.runs(op.text):
            rtl = 1 if RTL_RE.search(run) else 0
            tw.append(fitz.Point(ox + x, oy + op.y), run, font=font, fontsize=op.style.size, right_to_left=rtl)
            x += font.text_length(run, fontsize=op.style.size)
    # TextWriter's morph rotates in PDF's y-up sense, hence the negated angle
    morph = (fitz.Point(ox, oy), fitz.Matrix(-angle)) if abs(angle) > 0.01 else None
    for color, tw in writers.items():
        tw.write_text(page, color=color, morph=morph)


# ---------------------------------------------------------------------------
# planning edits
# ---------------------------------------------------------------------------
def _common_prefix(a: str, b: str) -> int:
    n = min(len(a), len(b))
    i = 0
    while i < n and a[i] == b[i]:
        i += 1
    return i


def _clean(text: str) -> str:
    return text.replace("\t", " ").replace("\r", "").translate(_SPACE_LIKE)


def plan_line_edit(page: fitz.Page, fonts: FontResolver, line: VLine, new_text: str,
                   override: dict | None = None) -> Plan | None:
    new_text = _clean(new_text).replace("\n", " ")
    pairs = line.pairs
    old = line.text
    if new_text == old and not override:
        return None
    d = line.dir
    O = tuple(pairs[0][0]["origin"])

    def t_of(c):
        return _local(c["origin"], O, d)[0]

    def y_of(c):
        return _local(c["origin"], O, d)[1]

    n_old, n_new = len(old), len(new_text)
    if override:
        p = s = 0
    else:
        p = _common_prefix(old, new_text)
        s = _common_prefix(old[::-1], new_text[::-1])
        s = min(s, n_old - p, n_new - p)
        # never start a rewrite on a virtual (synthetic) space
        while p > 0 and p < n_old and pairs[p][0].get("virtual"):
            p -= 1

    plan = Plan(origin=O, angle=math.degrees(math.atan2(d[1], d[0])))
    plan.redact = [char_kill_rect(c, sp, d) for c, sp in pairs[p:] if not c.get("virtual")]

    if p < n_old:
        anchor = pairs[p][0]
        t_start = t_of(anchor)
    else:
        anchor = pairs[-1][0]
        t_start = t_of(anchor) + _advance(anchor, d)
    ref_span = pairs[min(p, n_old - 1)][1]
    if override:
        ref_span = dominant_span(line.spans)
    styles: dict[int, Style] = {}

    def style_for(span):
        if id(span) not in styles:
            styles[id(span)] = fonts.span_style(page, span, override)
        return styles[id(span)]

    inserted = new_text[p:n_new - s]
    st = style_for(ref_span)
    if inserted:
        plan.ops.append(WriteOp(t_start, y_of(anchor), inserted, st))
    if s > 0:
        suffix = pairs[n_old - s:]
        delta = st.width(inserted) - (t_of(suffix[0][0]) - t_start)
        group: list[tuple[dict, dict]] = []

        def flush():
            if group:
                c0, sp0 = group[0]
                plan.ops.append(WriteOp(t_of(c0) + delta, y_of(c0),
                                        "".join(c["c"] for c, _ in group), style_for(sp0)))
                group.clear()

        for c, sp in suffix:
            # keep original gaps between segments: a virtual space starts a new group
            if group and (group[-1][1] is not sp or c.get("virtual")):
                flush()
            if c.get("virtual"):
                continue
            group.append((c, sp))
        flush()
    return plan


def _spread(vals: list[float]) -> float:
    return (max(vals) - min(vals)) if vals else 0


def plan_para_edit(page: fitz.Page, fonts: FontResolver, lines: list[VLine], new_text: str,
                   override: dict | None = None, align: str | None = None) -> Plan | None:
    if not lines:
        return None
    d = lines[0].dir
    O = tuple(lines[0].pairs[0][0]["origin"])
    plan = Plan(origin=O, angle=math.degrees(math.atan2(d[1], d[0])))
    for l in lines:
        plan.redact += [char_kill_rect(c, sp, d) for c, sp in l.pairs if not c.get("virtual")]
    new_text = _clean(new_text)
    if not new_text.strip():
        return plan

    st = fonts.span_style(page, dominant_span([s for l in lines for s in l.spans]), override)
    lefts, rights, bases = [], [], []
    for l in lines:
        vis = [c for c, _ in l.pairs if c["c"].strip() and not c.get("virtual")] or [l.pairs[0][0]]
        lx, ly = _local(vis[0]["origin"], O, d)
        rx = _local(vis[-1]["origin"], O, d)[0] + _advance(vis[-1], d)
        lefts.append(lx)
        rights.append(rx)
        bases.append(ly)
    if len(bases) > 1:
        diffs = sorted(b - a for a, b in zip(bases, bases[1:]) if b - a > 0.5)
        leading = diffs[len(diffs) // 2] if diffs else st.size * 1.2
    else:
        leading = st.size * 1.2
    left, right = min(lefts), max(rights)
    width = right - left
    tol = st.size * 0.6
    if not align:
        align = "left"
        if len(lines) > 1:
            body = rights[:-1]
            if _spread(lefts[1:] or lefts) < tol and len(lines) > 2 and _spread(body) < tol:
                align = "justify"
            elif _spread(lefts[1:] or lefts) < tol:
                align = "left"
            elif _spread(rights) < tol:
                align = "right"
            elif _spread([(a + b) / 2 for a, b in zip(lefts, rights)]) < tol:
                align = "center"
    wrap = len(lines) > 1
    first_indent = (lefts[0] - left) if align in ("left", "justify") else 0

    space_w = st.width(" ")
    out_lines: list[tuple[list[str], bool, float]] = []  # words, last-of-paragraph, indent
    for para in new_text.split("\n"):
        words = para.split(" ")
        cur: list[str] = []
        cur_w = 0.0
        indent = first_indent if not out_lines else 0
        for w in words:
            ww = st.width(w)
            add = ww if not cur else cur_w + space_w + ww
            if wrap and cur and add > width - indent + 0.5:
                out_lines.append((cur, False, indent))
                cur, cur_w, indent = [w], ww, 0
            else:
                cur.append(w)
                cur_w = add
        out_lines.append((cur, True, indent))

    for i, (words, last, indent) in enumerate(out_lines):
        y = bases[0] + i * leading
        text = " ".join(words)
        tw = st.width(text)
        if align == "center":
            x = left + (width - tw) / 2
        elif align == "right":
            x = right - tw
        else:
            x = left + indent
        if align == "justify" and not last and len(words) > 1:
            gap = (width - indent - sum(st.width(w) for w in words)) / (len(words) - 1)
            cx = x
            for w in words:
                plan.ops.append(WriteOp(cx, y, w, st))
                cx += st.width(w) + gap
        else:
            plan.ops.append(WriteOp(x, y, text, st))
    return plan


# ---------------------------------------------------------------------------
# high level operations (called inside store.mutate)
# ---------------------------------------------------------------------------
def _pick(items: list, idx: int, expect: str | None, text_of) -> int:
    """Index of the wanted item; if the page shifted, fall back to the nearest item with the expected text."""
    if 0 <= idx < len(items) and (expect is None or text_of(items[idx]) == expect):
        return idx
    if expect is not None:
        cands = [k for k, it in enumerate(items) if text_of(it) == expect]
        if cands:
            return min(cands, key=lambda k: abs(k - idx))
    raise EditError("This text changed since the page was loaded - please try again")


def _get_line(page: fitz.Page, line_id: int, expect: str | None) -> VLine:
    vl = visual_lines(raw_blocks(page))
    return vl[_pick(vl, line_id, expect, lambda v: v.text)]


def edit_line(doc: fitz.Document, pno: int, line_id: int, text: str,
              expect: str | None = None, override: dict | None = None) -> None:
    page = doc[pno]
    with unrotated(page):
        line = _get_line(page, line_id, expect)
        plan = plan_line_edit(page, FontResolver(doc), line, text, override)
        if plan:
            execute(page, [plan])


def edit_paragraph(doc: fitz.Document, pno: int, para_id: int, text: str, expect: str | None = None,
                   override: dict | None = None, align: str | None = None) -> None:
    page = doc[pno]
    with unrotated(page):
        vl = visual_lines(raw_blocks(page))
        paras = paragraphs(vl)
        para_id = _pick(paras, para_id, expect, lambda p: para_text(vl, p))
        plan = plan_para_edit(page, FontResolver(doc), [vl[i] for i in paras[para_id]], text, override, align)
        if plan:
            execute(page, [plan])


def erase_text_in_rect(doc: fitz.Document, pno: int, disp_rect) -> int:
    """Remove every glyph whose centre lies inside the rectangle."""
    page = doc[pno]
    with unrotated(page) as fr:
        rect = fr.rect_pdf(disp_rect)
        plan = Plan()
        for b in raw_blocks(page):
            if b.get("type") != 0:
                continue
            for l in b["lines"]:
                for sp in l["spans"]:
                    for c in sp["chars"]:
                        x0, y0, x1, y1 = c["bbox"]
                        if rect.contains(fitz.Point((x0 + x1) / 2, (y0 + y1) / 2)):
                            plan.redact.append(char_kill_rect(c, sp, l["dir"]))
        execute(page, [plan])
        return len(plan.redact)


def add_text(doc: fitz.Document, pno: int, x: float, y: float, text: str, font: str,
             size: float, color, bold: bool, italic: bool, align: str = "left",
             line_height: float = 1.2) -> None:
    page = doc[pno]
    with unrotated(page) as fr:
        st = FontResolver.user_style(font, size, color, bold, italic)
        O = fr.pt_pdf(x, y)
        lines = _clean(text).split("\n")
        widths = [st.width(t) for t in lines]
        box_w = max(widths) if widths else 0
        f0 = st.chain[0]
        asc, desc = (f0.ascender or 0.9), (f0.descender or -0.2)
        # same as CSS: half-leading above the font's content box, then the ascent
        first_base = ((line_height - (asc - desc)) / 2 + asc) * size
        ops = []
        for i, (t, w) in enumerate(zip(lines, widths)):
            dx = (box_w - w) / 2 if align == "center" else (box_w - w) if align == "right" else 0
            ops.append(WriteOp(dx, first_base + i * size * line_height, t, st))
        write_ops(page, ops, (O.x, O.y), fr.upright_angle())


def find_text(doc: fitz.Document, find: str, match_case: bool) -> list[dict]:
    """Search visual lines; returns display-space boxes per hit."""
    if not find:
        return []
    flags = 0 if match_case else re.IGNORECASE
    pattern = re.compile(re.escape(_clean(find)), flags)
    hits = []
    for pno in range(doc.page_count):
        page = doc[pno]
        fr = Frame(page.rotation_matrix, page.derotation_matrix, page.rotation)
        for v in visual_lines(raw_blocks(page)):
            for m in pattern.finditer(v.text):
                r = fitz.EMPTY_RECT()
                for c, _ in v.pairs[m.start():m.end()]:
                    if not c.get("virtual"):
                        r |= fitz.Rect(c["bbox"])
                if not r.is_empty:
                    hits.append({"page": pno, "bbox": fr.rect_disp(r)})
    return hits


def find_replace(doc: fitz.Document, find: str, replace: str, match_case: bool,
                 pages: list[int] | None = None) -> tuple[int, list[int]]:
    if not find:
        return 0, []
    pattern = re.compile(re.escape(_clean(find)), 0 if match_case else re.IGNORECASE)
    replace = _clean(replace).replace("\n", " ")
    total, changed = 0, []
    fonts = FontResolver(doc)
    for pno in (pages if pages is not None else range(doc.page_count)):
        page = doc[pno]
        with unrotated(page):
            plans = []
            for v in visual_lines(raw_blocks(page)):
                new, n = pattern.subn(lambda _m: replace, v.text)
                if n and (plan := plan_line_edit(page, fonts, v, new)):
                    plans.append(plan)
                    total += n
            if plans:
                execute(page, plans)
                changed.append(pno)
    return total, changed
