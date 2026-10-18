"""HTTP API. Endpoints are plain `def` so FastAPI runs them in its threadpool;
the PDF work itself is serialised by the store's locks."""

from __future__ import annotations

import json
from typing import Callable
from urllib.parse import quote

import pymupdf as fitz
from fastapi import APIRouter, File, Form, Query, UploadFile
from fastapi.responses import Response

from ..config import settings
from ..pdf import ops
from ..pdf import text as textops
from ..pdf.fonts import INDEX
from ..pdf.geometry import frame_of, hex_to_rgb
from ..store import InvalidDocument, PDF_LOCK, load_document, store
from . import schemas as S

router = APIRouter(prefix="/api")


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def _read_upload(file: UploadFile) -> bytes:
    chunks, total = [], 0
    while chunk := file.file.read(1024 * 1024):
        total += len(chunk)
        if total > settings.max_upload_bytes:
            raise InvalidDocument(f"File is larger than {settings.max_upload_mb} MB")
        chunks.append(chunk)
    if not total:
        raise InvalidDocument("Empty file")
    return b"".join(chunks)


def _state(sid: str, meta: dict | None = None, changed: list[int] | None = None, **extra) -> dict:
    meta = meta or store.meta(sid)

    def collect(doc: fitz.Document, m: dict) -> dict:
        return {
            "id": sid, "name": m["name"], "version": m["version"],
            "can_undo": bool(m["undo"]), "can_redo": bool(m["redo"]),
            "changed": changed, **ops.doc_info(doc), **extra,
        }

    return store.read(sid, collect)


def _mutate(sid: str, fn: Callable[[fitz.Document], object], pages: list[int] | None = None,
            key: str = "result") -> dict:
    """Run fn as an undoable edit. `pages` lists the pages that need re-rendering
    (None = everything, e.g. after page reordering)."""
    result, meta = store.mutate(sid, fn)
    return _state(sid, meta, changed=pages, **({key: result} if result is not None else {}))


def _style(style: S.TextStyle | None) -> dict | None:
    if not style or style.is_empty():
        return None
    d = style.model_dump()
    d["color_rgb"] = hex_to_rgb(d.pop("color"))
    return d


# ---------------------------------------------------------------------------
# meta
# ---------------------------------------------------------------------------
@router.get("/health")
def health():
    return {"ok": True, "pymupdf": fitz.VersionBind}


@router.get("/fonts")
def fonts():
    return INDEX.user_families()


# ---------------------------------------------------------------------------
# documents
# ---------------------------------------------------------------------------
@router.post("/documents")
def upload(file: UploadFile = File(...), password: str | None = Form(None)):
    data = _read_upload(file)
    sid = store.create(data, file.filename or "document.pdf", password)
    return _state(sid)


@router.post("/documents/blank")
def blank(body: S.Blank):
    r = fitz.paper_rect(body.size + ("-l" if body.landscape else ""))
    with PDF_LOCK:
        d = fitz.open()
        d.new_page(width=r.width, height=r.height)
        data = d.tobytes()
    return _state(store.create(data, "Untitled.pdf"))


@router.get("/documents/{sid}")
def get_doc(sid: str):
    return _state(sid)


@router.delete("/documents/{sid}")
def delete_doc(sid: str):
    store.delete(sid)
    return {"ok": True}


@router.post("/documents/{sid}/undo")
def undo(sid: str):
    return _state(sid, store.undo(sid))


@router.post("/documents/{sid}/redo")
def redo(sid: str):
    return _state(sid, store.redo(sid))


@router.get("/documents/{sid}/pages/{pno}/image")
def page_image(sid: str, pno: int, scale: float = Query(1.5, gt=0), v: int | None = None,
               fmt: str = Query("png", pattern="^(png|jpg)$")):
    scale = min(scale, settings.max_render_scale)

    def render(doc, _m):
        if not 0 <= pno < doc.page_count:
            raise textops.EditError("Page out of range")
        return ops.render_page(doc, pno, scale, fmt)

    data = store.read(sid, render)
    # URL carries the version, so the browser may cache it for a while
    cache = "private, max-age=3600" if v is not None else "no-store"
    return Response(data, media_type="image/png" if fmt == "png" else "image/jpeg",
                    headers={"Cache-Control": cache})


@router.get("/documents/{sid}/pages/{pno}/text")
def page_text(sid: str, pno: int):
    return store.read(sid, lambda doc, _m: textops.page_text_model(doc[pno], frame_of(doc[pno])))


@router.get("/documents/{sid}/pages/{pno}/objects")
def page_objects(sid: str, pno: int):
    return store.read(sid, lambda doc, _m: ops.page_objects(doc, pno))


@router.get("/documents/{sid}/search")
def search(sid: str, q: str, match_case: bool = False):
    return store.read(sid, lambda doc, _m: textops.find_text(doc, q, match_case))


# ---------------------------------------------------------------------------
# text editing
# ---------------------------------------------------------------------------
@router.post("/documents/{sid}/text/line")
def edit_line(sid: str, body: S.LineEdit):
    return _mutate(sid, lambda doc: textops.edit_line(doc, body.page, body.line, body.text,
                                                      body.expect, _style(body.style)), [body.page])


@router.post("/documents/{sid}/text/paragraph")
def edit_paragraph(sid: str, body: S.ParagraphEdit):
    return _mutate(sid, lambda doc: textops.edit_paragraph(doc, body.page, body.paragraph, body.text,
                                                           body.expect, _style(body.style), body.align),
                   [body.page])


@router.post("/documents/{sid}/text/add")
def add_text(sid: str, body: S.AddText):
    return _mutate(sid, lambda doc: textops.add_text(
        doc, body.page, body.x, body.y, body.text, body.font, body.size,
        hex_to_rgb(body.color), body.bold, body.italic, body.align, body.line_height), [body.page])


@router.post("/documents/{sid}/text/erase")
def erase_text(sid: str, body: S.AreaOp):
    return _mutate(sid, lambda doc: textops.erase_text_in_rect(doc, body.page, body.rect), [body.page], "erased")


@router.post("/documents/{sid}/text/replace")
def replace(sid: str, body: S.Replace):
    box: dict = {}

    def run(doc):
        count, pages = textops.find_replace(doc, body.find, body.replace, body.match_case)
        box["pages"] = pages
        return count

    st = _mutate(sid, run, None, "replaced")
    st["changed"] = box.get("pages", [])
    return st


# ---------------------------------------------------------------------------
# drawing, images, erase areas
# ---------------------------------------------------------------------------
@router.post("/documents/{sid}/draw")
def draw(sid: str, body: S.Draw):
    return _mutate(sid, lambda doc: ops.draw_shape(doc, body.page, body.kind, body.points, body.stroke,
                                                   body.fill, body.width, body.opacity), [body.page])


@router.post("/documents/{sid}/redact")
def redact(sid: str, body: S.Redact):
    return _mutate(sid, lambda doc: ops.redact_area(doc, body.page, body.rect, body.fill, body.text,
                                                    body.images, body.graphics), [body.page])


@router.post("/documents/{sid}/images")
def add_image(sid: str, page: int = Form(...), rect: str = Form(...), file: UploadFile = File(...)):
    data = _read_upload(file)
    r = json.loads(rect)
    return _mutate(sid, lambda doc: ops.insert_image(doc, page, r, data), [page])


@router.post("/documents/{sid}/images/replace")
def replace_image(sid: str, page: int = Form(...), xref: int = Form(...), file: UploadFile = File(...)):
    data = _read_upload(file)
    return _mutate(sid, lambda doc: ops.replace_image(doc, page, xref, data), None)


@router.post("/documents/{sid}/images/delete")
def delete_image(sid: str, body: S.XrefOp):
    return _mutate(sid, lambda doc: ops.delete_image(doc, body.page, body.xref), None)


# ---------------------------------------------------------------------------
# annotations & forms
# ---------------------------------------------------------------------------
@router.post("/documents/{sid}/annotations/markup")
def markup(sid: str, body: S.Markup):
    return _mutate(sid, lambda doc: ops.markup(doc, body.page, body.rect, body.kind, body.color),
                   [body.page], "marked")


@router.post("/documents/{sid}/annotations/note")
def note(sid: str, body: S.Note):
    return _mutate(sid, lambda doc: ops.add_note(doc, body.page, body.x, body.y, body.text, body.color),
                   [body.page])


@router.post("/documents/{sid}/annotations/delete")
def delete_annot(sid: str, body: S.XrefOp):
    return _mutate(sid, lambda doc: ops.delete_annot(doc, body.page, body.xref), [body.page])


@router.post("/documents/{sid}/forms/set")
def set_field(sid: str, body: S.FieldSet):
    return _mutate(sid, lambda doc: ops.set_field(doc, body.page, body.xref, body.value), [body.page])


@router.post("/documents/{sid}/flatten")
def flatten(sid: str):
    return _mutate(sid, ops.flatten_annots, None)


# ---------------------------------------------------------------------------
# pages & document
# ---------------------------------------------------------------------------
@router.post("/documents/{sid}/pages/rotate")
def rotate(sid: str, body: S.Rotate):
    return _mutate(sid, lambda doc: ops.rotate_pages(doc, body.pages, body.delta), None)


@router.post("/documents/{sid}/pages/delete")
def delete_pages(sid: str, body: S.Pages):
    return _mutate(sid, lambda doc: ops.delete_pages(doc, body.pages), None)


@router.post("/documents/{sid}/pages/insert-blank")
def insert_blank(sid: str, body: S.InsertBlank):
    return _mutate(sid, lambda doc: ops.insert_blank(doc, body.at, body.width, body.height), None)


@router.post("/documents/{sid}/pages/reorder")
def reorder(sid: str, body: S.Reorder):
    return _mutate(sid, lambda doc: ops.reorder(doc, body.order), None)


@router.post("/documents/{sid}/pages/duplicate")
def duplicate(sid: str, body: S.PageRef):
    return _mutate(sid, lambda doc: ops.duplicate_page(doc, body.page), None)


@router.post("/documents/{sid}/pages/insert-file")
def insert_file(sid: str, at: int = Form(...), file: UploadFile = File(...), password: str | None = Form(None)):
    data = _read_upload(file)
    with PDF_LOCK:
        src = load_document(data, file.filename or "", password)
    try:
        return _mutate(sid, lambda doc: ops.insert_document(doc, src, at), None)
    finally:
        with PDF_LOCK:
            src.close()


@router.post("/documents/{sid}/metadata")
def metadata(sid: str, body: S.Metadata):
    if body.name:
        store.rename(sid, body.name)
    fields = body.model_dump(exclude={"name"}, exclude_none=True)
    if not fields:
        return _state(sid)
    return _mutate(sid, lambda doc: ops.set_metadata(doc, fields), [])


@router.post("/documents/{sid}/export")
def export(sid: str, body: S.Export):
    data, name = store.read(sid, lambda doc, m: (ops.export(doc, body.pages, body.password, body.flatten),
                                                 m["name"]))
    return Response(data, media_type="application/pdf", headers={
        "Content-Disposition": f"attachment; filename*=UTF-8''{quote(name)}",
        "Cache-Control": "no-store",
    })
