"""End-to-end API tests: `python -m pytest -q`"""

import io
import os
import tempfile

os.environ.setdefault("PDFE_DATA_DIR", tempfile.mkdtemp(prefix="pdfe-test-"))

import pymupdf as fitz  # noqa: E402
import pytest  # noqa: E402
from fastapi.testclient import TestClient  # noqa: E402

from app.main import app  # noqa: E402
from app.pdf.text import raw_blocks, visual_lines  # noqa: E402


def make_pdf() -> bytes:
    doc = fitz.open()
    page = doc.new_page()
    page.insert_text((72, 100), "Invoice number: 12345", fontsize=14)
    page.insert_text((72, 120), "Total due: $1,250.00", fontsize=14)
    page.draw_rect(fitz.Rect(60, 80, 400, 130), color=(0, 0, 1))
    page.insert_textbox(fitz.Rect(72, 200, 350, 300),
                        "A paragraph of text that wraps over several lines so that we can "
                        "test paragraph editing and reflow of the content.", fontsize=11)
    widget = fitz.Widget()
    widget.field_name = "name"
    widget.field_type = fitz.PDF_WIDGET_TYPE_TEXT
    widget.rect = fitz.Rect(72, 400, 300, 420)
    page.add_widget(widget)
    p2 = doc.new_page()
    p2.insert_text((72, 100), "Second page", fontsize=14)
    return doc.tobytes()


@pytest.fixture(scope="module")
def client():
    with TestClient(app) as c:
        yield c


@pytest.fixture()
def sid(client):
    r = client.post("/api/documents", files={"file": ("test.pdf", make_pdf(), "application/pdf")})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def text_of(client, sid, page=0):
    return client.get(f"/api/documents/{sid}/pages/{page}/text").json()


def pdf_text(client, sid, page=0):
    """Text of the *exported* file, grouped into visual lines."""
    data = client.post(f"/api/documents/{sid}/export", json={}).content
    page = fitz.open("pdf", data)[page]
    return "\n".join(v.text for v in visual_lines(raw_blocks(page)))


def test_health(client):
    h = client.get("/api/health").json()
    assert h["ok"] and h["session_ttl_minutes"] > 0 and h["max_upload_mb"] > 0


def test_static_pages(client):
    home = client.get("/")
    assert home.status_code == 200 and "Privacy policy" in home.text
    privacy = client.get("/privacy.html")
    assert privacy.status_code == 200 and "Privacy Policy" in privacy.text


def test_delete_document(client, sid):
    assert client.delete(f"/api/documents/{sid}").json()["ok"]
    assert client.get(f"/api/documents/{sid}").status_code == 404


def test_edit_line_replaces_real_text(client, sid):
    lines = text_of(client, sid)["lines"]
    line = next(l for l in lines if l["text"].startswith("Total due"))
    r = client.post(f"/api/documents/{sid}/text/line",
                    json={"page": 0, "line": line["id"], "expect": line["text"], "text": "Total due: $99.00"})
    assert r.status_code == 200, r.text
    assert r.json()["changed"] == [0]
    text = pdf_text(client, sid)
    assert "Total due: $99.00" in text
    assert "1,250" not in text  # really gone from the file, not covered


def test_delete_line_and_undo_redo(client, sid):
    line = next(l for l in text_of(client, sid)["lines"] if l["text"].startswith("Invoice"))
    client.post(f"/api/documents/{sid}/text/line", json={"page": 0, "line": line["id"], "text": ""})
    assert "Invoice" not in pdf_text(client, sid)
    st = client.post(f"/api/documents/{sid}/undo").json()
    assert st["can_redo"]
    assert "Invoice" in pdf_text(client, sid)
    client.post(f"/api/documents/{sid}/redo")
    assert "Invoice" not in pdf_text(client, sid)


def test_paragraph_edit(client, sid):
    para = next(p for p in text_of(client, sid)["paragraphs"] if p["text"].startswith("A paragraph"))
    new = "Completely new paragraph text " * 4
    r = client.post(f"/api/documents/{sid}/text/paragraph",
                    json={"page": 0, "paragraph": para["id"], "expect": para["text"], "text": new.strip()})
    assert r.status_code == 200, r.text
    text = pdf_text(client, sid)
    assert "Completely new paragraph" in text and "reflow" not in text


def test_erase_area_keeps_graphics(client, sid):
    r = client.post(f"/api/documents/{sid}/text/erase", json={"page": 0, "rect": [60, 80, 400, 130]})
    assert r.json()["erased"] > 20
    data = client.post(f"/api/documents/{sid}/export", json={}).content
    page = fitz.open("pdf", data)[0]
    assert "Invoice" not in page.get_text()
    assert page.get_drawings(), "vector box must survive text erasing"


def test_add_text_shapes_images_annots(client, sid):
    base = f"/api/documents/{sid}"
    assert client.post(f"{base}/text/add", json={"page": 0, "x": 100, "y": 500, "text": "Hello\nWorld"}).status_code == 200
    assert client.post(f"{base}/draw", json={"page": 0, "kind": "ink", "points": [[10, 10], [20, 30], [40, 20]]}).status_code == 200
    assert client.post(f"{base}/draw", json={"page": 0, "kind": "arrow", "points": [[10, 10], [100, 100]]}).status_code == 200
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 20, 20), False)
    pix.clear_with(200)
    r = client.post(f"{base}/images", data={"page": "0", "rect": "[300,500,400,600]"},
                    files={"file": ("x.png", pix.tobytes("png"), "image/png")})
    assert r.status_code == 200, r.text
    objs = client.get(f"{base}/pages/0/objects").json()
    assert objs["images"] and objs["fields"]
    r = client.post(f"{base}/annotations/markup", json={"page": 0, "rect": [0, 80, 600, 130], "kind": "highlight"})
    assert r.json()["marked"] >= 1
    annots = client.get(f"{base}/pages/0/objects").json()["annots"]
    assert client.post(f"{base}/annotations/delete", json={"page": 0, "xref": annots[0]["xref"]}).status_code == 200
    assert "Hello" in pdf_text(client, sid)


def test_forms_and_pages(client, sid):
    base = f"/api/documents/{sid}"
    field = client.get(f"{base}/pages/0/objects").json()["fields"][0]
    client.post(f"{base}/forms/set", json={"page": 0, "xref": field["xref"], "value": "Jane"})
    assert client.get(f"{base}/pages/0/objects").json()["fields"][0]["value"] == "Jane"
    st = client.post(f"{base}/pages/rotate", json={"pages": [1], "delta": 90}).json()
    assert st["pages"][1]["rotation"] == 90
    st = client.post(f"{base}/pages/reorder", json={"order": [1, 0]}).json()
    assert "Second page" in pdf_text(client, sid, 0)
    st = client.post(f"{base}/pages/insert-blank", json={"at": 2}).json()
    assert st["page_count"] == 3
    st = client.post(f"{base}/pages/delete", json={"pages": [2]}).json()
    assert st["page_count"] == 2
    st = client.post(f"{base}/pages/duplicate", json={"page": 0}).json()
    assert st["page_count"] == 3


def test_rotated_page_edit(client, sid):
    base = f"/api/documents/{sid}"
    client.post(f"{base}/pages/rotate", json={"pages": [1], "delta": 90})
    line = text_of(client, sid, 1)["lines"][0]
    client.post(f"{base}/text/line", json={"page": 1, "line": line["id"], "text": "Second page EDITED"})
    assert "Second page EDITED" in pdf_text(client, sid, 1)
    client.post(f"{base}/text/add", json={"page": 1, "x": 50, "y": 50, "text": "Upright"})
    lines = text_of(client, sid, 1)["lines"]
    upright = next(l for l in lines if l["text"] == "Upright")
    assert upright["angle"] == 0


def test_find_replace_and_search(client, sid):
    base = f"/api/documents/{sid}"
    hits = client.get(f"{base}/search", params={"q": "page"}).json()
    assert hits
    r = client.post(f"{base}/text/replace", json={"find": "Second", "replace": "2nd"}).json()
    assert r["replaced"] == 1
    assert "2nd page" in pdf_text(client, sid, 1)


def test_export_with_password(client, sid):
    data = client.post(f"/api/documents/{sid}/export", json={"password": "s3cret", "pages": [0]}).content
    doc = fitz.open("pdf", data)
    assert doc.needs_pass and doc.authenticate("s3cret") and doc.page_count == 1


def test_encrypted_upload_needs_password(client):
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), "secret")
    data = doc.tobytes(encryption=fitz.PDF_ENCRYPT_AES_256, user_pw="pw", owner_pw="pw")
    r = client.post("/api/documents", files={"file": ("e.pdf", data, "application/pdf")})
    assert r.status_code == 401 and r.json()["error"] == "password_required"
    r = client.post("/api/documents", files={"file": ("e.pdf", data, "application/pdf")}, data={"password": "pw"})
    assert r.status_code == 200


def test_image_upload_becomes_pdf(client):
    pix = fitz.Pixmap(fitz.csRGB, fitz.IRect(0, 0, 50, 50), False)
    pix.clear_with(128)
    r = client.post("/api/documents", files={"file": ("pic.png", pix.tobytes("png"), "image/png")})
    assert r.status_code == 200 and r.json()["page_count"] == 1


def test_bad_session_and_upload(client):
    assert client.get("/api/documents/" + "0" * 32).status_code == 404
    assert client.get("/api/documents/../../etc").status_code == 404
    r = client.post("/api/documents", files={"file": ("x.pdf", b"not a pdf", "application/pdf")})
    assert r.status_code == 400
