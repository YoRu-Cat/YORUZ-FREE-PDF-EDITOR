"""Non-text editing operations: drawing, images, annotations, forms, redaction,
page management, export. Every function receives display-space coordinates
and runs inside SessionStore.mutate (or .read for queries)."""

from __future__ import annotations

import math

import pymupdf as fitz

from .geometry import frame_of, hex_to_rgb, unrotated
from .text import EditError, raw_blocks, visual_lines


# ---------------------------------------------------------------------------
# page info / rendering
# ---------------------------------------------------------------------------
def doc_info(doc: fitz.Document) -> dict:
    return {
        "page_count": doc.page_count,
        "pages": [{"w": round(p.rect.width, 2), "h": round(p.rect.height, 2), "rotation": p.rotation}
                  for p in doc],
        "metadata": {k: v for k, v in (doc.metadata or {}).items()
                     if k in ("title", "author", "subject", "keywords", "creator", "producer")},
        "has_forms": any(True for p in doc for _ in p.widgets()),
    }


def render_page(doc: fitz.Document, pno: int, scale: float, fmt: str = "png") -> bytes:
    page = doc[pno]
    pix = page.get_pixmap(matrix=fitz.Matrix(scale, scale), annots=True, alpha=False)
    if fmt == "jpg":
        return pix.tobytes("jpg", jpg_quality=85)
    return pix.tobytes("png")


def page_objects(doc: fitz.Document, pno: int) -> dict:
    """Images, annotations and form fields of a page in display space."""
    page = doc[pno]
    fr = frame_of(page)
    images = []
    for info in page.get_image_info(xrefs=True):
        if info.get("xref"):
            images.append({"xref": info["xref"], "bbox": fr.rect_disp(info["bbox"]),
                           "w": info.get("width"), "h": info.get("height")})
    annots = []
    for a in page.annots():
        annots.append({"xref": a.xref, "type": a.type[1], "bbox": fr.rect_disp(a.rect),
                       "content": a.info.get("content", "")})
    fields = []
    for w in page.widgets():
        fields.append({
            "xref": w.xref, "name": w.field_name, "type": w.field_type_string,
            "value": w.field_value, "bbox": fr.rect_disp(w.rect),
            "choices": w.choice_values or [],
            "on_state": w.on_state() if w.field_type in (fitz.PDF_WIDGET_TYPE_CHECKBOX,
                                                         fitz.PDF_WIDGET_TYPE_RADIOBUTTON) else None,
            "readonly": bool(w.field_flags & 1),
            "multiline": bool(w.field_flags & 4096),
            "label": w.field_label or "",
        })
    return {"images": images, "annots": annots, "fields": fields}


# ---------------------------------------------------------------------------
# drawing (written permanently into the page content)
# ---------------------------------------------------------------------------
def draw_shape(doc: fitz.Document, pno: int, kind: str, points: list[list[float]],
               stroke: str | None, fill: str | None, width: float, opacity: float) -> None:
    page = doc[pno]
    if len(points) < 2 and kind != "ink":
        raise EditError("Shape needs two points")
    with unrotated(page) as fr:
        pts = [fr.pt_pdf(x, y) for x, y in points]
        sc, fc = hex_to_rgb(stroke), hex_to_rgb(fill)
        shape = page.new_shape()
        close = False
        if kind in ("rect", "whiteout", "highlight"):
            shape.draw_rect(fitz.Rect(pts[0], pts[1]).normalize())
            close = True
        elif kind == "ellipse":
            shape.draw_oval(fitz.Rect(pts[0], pts[1]).normalize())
            close = True
        elif kind == "line":
            shape.draw_line(pts[0], pts[1])
        elif kind == "arrow":
            a, b = pts[0], pts[-1]
            shape.draw_line(a, b)
            ang = math.atan2(b.y - a.y, b.x - a.x)
            head = max(6.0, width * 4)
            for s in (+1, -1):
                t = ang + math.pi - s * math.radians(28)
                shape.draw_line(b, fitz.Point(b.x + head * math.cos(t), b.y + head * math.sin(t)))
        elif kind == "ink":
            if len(pts) == 1:
                pts.append(pts[0] + (0.01, 0.01))
            shape.draw_polyline(pts)
        else:
            raise EditError(f"Unknown shape: {kind}")
        shape.finish(color=sc, fill=fc if close else None, width=width if sc else 0,
                     stroke_opacity=opacity, fill_opacity=opacity, lineCap=1, lineJoin=1, closePath=close)
        shape.commit()


def insert_image(doc: fitz.Document, pno: int, rect: list[float], data: bytes) -> None:
    page = doc[pno]
    with unrotated(page) as fr:
        try:
            page.insert_image(fr.rect_pdf(rect), stream=data, keep_proportion=False, rotate=fr.rotation)
        except Exception as e:  # noqa: BLE001
            raise EditError(f"Unsupported image: {e}") from e


def delete_image(doc: fitz.Document, pno: int, xref: int) -> None:
    doc[pno].delete_image(xref)


def replace_image(doc: fitz.Document, pno: int, xref: int, data: bytes) -> None:
    try:
        doc[pno].replace_image(xref, stream=data)
    except Exception as e:  # noqa: BLE001
        raise EditError(f"Unsupported image: {e}") from e


# ---------------------------------------------------------------------------
# erase / redact areas (true removal from the content stream)
# ---------------------------------------------------------------------------
def redact_area(doc: fitz.Document, pno: int, rect: list[float], fill: str | None,
                text: bool, images: bool, graphics: bool) -> None:
    page = doc[pno]
    with unrotated(page) as fr:
        color = hex_to_rgb(fill)
        page.add_redact_annot(fr.rect_pdf(rect), fill=color if color else False, cross_out=False)
        page.apply_redactions(
            images=fitz.PDF_REDACT_IMAGE_PIXELS if images else fitz.PDF_REDACT_IMAGE_NONE,
            graphics=fitz.PDF_REDACT_LINE_ART_REMOVE_IF_COVERED if graphics else fitz.PDF_REDACT_LINE_ART_NONE,
            text=fitz.PDF_REDACT_TEXT_REMOVE if text else fitz.PDF_REDACT_TEXT_NONE,
        )


# ---------------------------------------------------------------------------
# annotations
# ---------------------------------------------------------------------------
def markup(doc: fitz.Document, pno: int, rect: list[float], kind: str, color: str | None) -> int:
    page = doc[pno]
    with unrotated(page) as fr:
        area = fr.rect_pdf(rect)
        quads = []
        for v in visual_lines(raw_blocks(page)):
            r = fitz.EMPTY_RECT()
            for c, _ in v.pairs:
                if c.get("virtual") or not c["c"].strip():
                    continue
                x0, y0, x1, y1 = c["bbox"]
                if area.contains(fitz.Point((x0 + x1) / 2, (y0 + y1) / 2)):
                    r |= fitz.Rect(c["bbox"])
            if not r.is_empty:
                quads.append(r.quad)
        if not quads:
            return 0
        fn = {"highlight": page.add_highlight_annot, "underline": page.add_underline_annot,
              "strikeout": page.add_strikeout_annot, "squiggly": page.add_squiggly_annot}.get(kind)
        if fn is None:
            raise EditError(f"Unknown markup: {kind}")
        annot = fn(quads=quads)
        rgb = hex_to_rgb(color)
        if rgb:
            annot.set_colors(stroke=rgb)
        annot.update()
        return len(quads)


def add_note(doc: fitz.Document, pno: int, x: float, y: float, text: str, color: str | None) -> None:
    page = doc[pno]
    with unrotated(page) as fr:
        annot = page.add_text_annot(fr.pt_pdf(x, y), text, icon="Comment")
        rgb = hex_to_rgb(color)
        if rgb:
            annot.set_colors(stroke=rgb)
        annot.update()


def delete_annot(doc: fitz.Document, pno: int, xref: int) -> None:
    page = doc[pno]
    for a in page.annots():
        if a.xref == xref:
            page.delete_annot(a)
            return
    raise EditError("Annotation not found")


def flatten_annots(doc: fitz.Document) -> None:
    """Burn annotations + form fields into page content (no longer editable)."""
    doc.bake(annots=True, widgets=True)


# ---------------------------------------------------------------------------
# forms
# ---------------------------------------------------------------------------
def set_field(doc: fitz.Document, pno: int, xref: int, value) -> None:
    page = doc[pno]
    for w in page.widgets():
        if w.xref != xref:
            continue
        t = w.field_type
        if t == fitz.PDF_WIDGET_TYPE_CHECKBOX:
            w.field_value = w.on_state() if value else "Off"
        elif t == fitz.PDF_WIDGET_TYPE_RADIOBUTTON:
            w.field_value = w.on_state() if value else "Off"
        else:
            w.field_value = "" if value is None else str(value)
        w.update()
        return
    raise EditError("Form field not found")


# ---------------------------------------------------------------------------
# pages
# ---------------------------------------------------------------------------
def _check_pages(doc: fitz.Document, pages: list[int]) -> list[int]:
    pages = sorted(set(pages))
    if not pages or any(p < 0 or p >= doc.page_count for p in pages):
        raise EditError("Invalid page selection")
    return pages


def rotate_pages(doc: fitz.Document, pages: list[int], delta: int) -> None:
    if delta % 90:
        raise EditError("Rotation must be a multiple of 90")
    for p in _check_pages(doc, pages):
        page = doc[p]
        page.set_rotation((page.rotation + delta) % 360)


def delete_pages(doc: fitz.Document, pages: list[int]) -> None:
    pages = _check_pages(doc, pages)
    if len(pages) >= doc.page_count:
        raise EditError("A document must keep at least one page")
    doc.delete_pages(pages)


def insert_blank(doc: fitz.Document, at: int, width: float | None, height: float | None) -> None:
    ref = doc[min(max(at - 1, 0), doc.page_count - 1)].rect if doc.page_count else fitz.paper_rect("a4")
    doc.new_page(pno=at if at < doc.page_count else -1,
                 width=width or ref.width, height=height or ref.height)


def reorder(doc: fitz.Document, order: list[int]) -> None:
    if sorted(order) != list(range(doc.page_count)):
        raise EditError("Order must be a permutation of all pages")
    doc.select(order)


def duplicate_page(doc: fitz.Document, pno: int) -> None:
    _check_pages(doc, [pno])
    doc.fullcopy_page(pno, pno + 1 if pno + 1 < doc.page_count else -1)


def insert_document(doc: fitz.Document, src: fitz.Document, at: int) -> None:
    doc.insert_pdf(src, start_at=at if at < doc.page_count else -1)


def set_metadata(doc: fitz.Document, meta: dict) -> None:
    cur = dict(doc.metadata or {})
    for k in ("title", "author", "subject", "keywords", "creator", "producer"):
        if k in meta:
            cur[k] = str(meta[k] or "")
    doc.set_metadata(cur)


# ---------------------------------------------------------------------------
# export
# ---------------------------------------------------------------------------
def export(doc: fitz.Document, pages: list[int] | None = None, password: str | None = None,
           flatten: bool = False) -> bytes:
    out = fitz.open("pdf", doc.tobytes())
    if pages:
        pages = [p for p in pages if 0 <= p < out.page_count]
        if pages:
            out.select(pages)
    if flatten:
        out.bake(annots=True, widgets=True)
    try:
        out.subset_fonts()
    except Exception:
        pass
    kw = dict(garbage=3, deflate=True, deflate_fonts=True, clean=False)
    if password:
        kw.update(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw=password, owner_pw=password,
                  permissions=int(fitz.PDF_PERM_ACCESSIBILITY | fitz.PDF_PERM_PRINT | fitz.PDF_PERM_COPY
                                  | fitz.PDF_PERM_ANNOTATE | fitz.PDF_PERM_FORM | fitz.PDF_PERM_PRINT_HQ))
    data = out.tobytes(**kw)
    out.close()
    return data
