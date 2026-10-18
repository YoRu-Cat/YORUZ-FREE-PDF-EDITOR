# Developer guide

How the editor works, how to run it, and how to host your own copy. For the user-facing overview, see the [README](README.md).

A web-based PDF editor that changes the **actual content of the PDF**. Edited and erased text is removed from the page's content stream rather than covered with white boxes, so copy/paste, search and text extraction in the downloaded file show only the new text.

## Features

| Area | What you can do |
|---|---|
| **Edit text** | Click any line to retype it in place, or switch to *Paragraph* mode to rewrite a whole block with automatic reflow (left / centre / right / justified alignment is detected). Font, size, colour, bold and italic are kept, or you can change them. |
| **Erase text** | Drag over text to delete those glyphs from the file. Images and vector graphics stay. |
| **Erase / redact area** | Permanently remove text, images and drawings inside a box, with a white, black or no fill. |
| **Add content** | Text boxes (standard and installed system fonts), images, signatures (drawn or uploaded, optionally remembered), freehand drawing, rectangles, ellipses, lines, arrows, whiteout. |
| **Annotate** | Highlight, underline, strikeout and squiggly markup; comments; delete existing annotations. |
| **Forms** | Fill text fields, checkboxes, radio buttons and dropdowns; flatten forms. |
| **Images** | Replace or delete images that are already in the PDF. |
| **Pages** | Reorder by dragging thumbnails, rotate, duplicate, delete, insert blank pages, merge in other PDFs or images, extract pages. |
| **Document** | Find with highlighted results, replace across all pages, undo/redo (50 steps), metadata, AES-256 password-protected export, opening encrypted PDFs. |
| **Input formats** | PDF, plus images, XPS, EPUB, CBZ, FB2 and SVG (converted to PDF on open). |

### How text editing works

1. MuPDF extracts every character with its position, glyph box and font. The characters are grouped into lines and paragraphs **by position on the page**, not by the order they appear in the file. Text written by earlier edits therefore merges back into its line.
2. The editor compares the old and new text. Only the characters from the first change onwards are touched, so the unchanged start of the line keeps its original glyphs exactly.
3. Those glyphs are removed with a *text-only* redaction. Images, vector graphics and neighbouring lines are not affected, and nothing is painted over the area.
4. The new text is written on the same baseline. The font is chosen per character, in this order:
   1. the PDF's own embedded font (when it contains every glyph needed);
   2. an installed system font with the same name or family and style;
   3. the closest standard PDF font (Helvetica, Times or Courier);
   4. Unicode fallback fonts.

   The unchanged end of the line is shifted to keep its original spacing.

## Architecture

```
browser (static/, no build step)         server (app/, FastAPI)
┌──────────────────────────────┐  JSON  ┌────────────────────────────────────────┐
│ app.js    shell, dialogs,    │ ─────▶ │ api/routes.py   HTTP endpoints         │
│           shortcuts, queue   │  PNG   │ api/schemas.py  request validation     │
│ viewer.js pages, lazy render,│ ◀───── │ pdf/text.py     text model + editing   │
│           thumbnails, zoom   │        │ pdf/ops.py      draw, images, annots,  │
│ tools.js  editors, drag/draw │        │                 forms, pages, export   │
│ api.js    REST client        │        │ pdf/fonts.py    font discovery/matching│
│ ui.js     icons, modals      │        │ pdf/geometry.py rotated-page coords    │
└──────────────────────────────┘        │ store.py        sessions, versions,    │
                                        │                 undo/redo, locking     │
                                        │ config.py       env-var settings       │
                                        └──────────────┬─────────────────────────┘
                                                       │
                                     <PDFE_DATA_DIR>/sessions/<id>/
                                        meta.json, v000001.pdf, v000002.pdf, …
```

- **The server is the source of truth.** The browser shows server-rendered page images with clickable text and object overlays. Every edit is one API call that returns the list of pages that need re-rendering.
- **Disk-backed sessions.** Each document is a folder holding one complete PDF per version, which provides undo/redo. Sessions survive restarts and keep memory bounded. Idle sessions are deleted after `PDFE_SESSION_TTL_MINUTES`.
- **Concurrency.** MuPDF is not thread-safe, so PDF work in each process runs under one lock. A per-session file lock keeps several worker processes from conflicting, so `--workers N` is safe.
- **Coordinates.** The API always uses *display space* (the page as it appears, after rotation). The engine converts to the PDF's unrotated space, so editing works on rotated pages.

## Run locally

```bash
pip install -r requirements.txt
python run.py            # opens http://localhost:8000
```

On Windows you can double-click `start.bat` instead.

Run the tests with:

```bash
pip install -r requirements-dev.txt
python -m pytest -q
```

Interactive API docs are at `/api/docs`.

## Deploy

### One click on Render (free tier)

[![Deploy to Render](https://render.com/images/deploy-to-render-button.svg)](https://render.com/deploy?repo=https://github.com/YoRu-Cat/FREE-PDF-EDITOR)

`render.yaml` builds the Dockerfile on a free web service. Every push to `main` redeploys automatically.

On the free tier:
- the service sleeps after 15 minutes without traffic, so the first request after that takes about 30 seconds;
- open documents are lost when the service restarts. For persistent storage, use a paid plan with a disk mounted at `/data`.

The same Dockerfile also runs on Railway, Fly.io, Koyeb and Google Cloud Run. All of them set `$PORT`, which the container uses.

### Docker (recommended)

```bash
docker compose up -d --build     # http://<host>:8000
```

The image installs free fonts that are metric-compatible with common ones (Liberation for Arial/Times/Courier, Carlito/Caladea for Calibri/Cambria, plus DejaVu and Noto). These matter when a PDF doesn't embed its fonts. The image runs as a non-root user, stores data in the `/data` volume and has a health check at `/api/health`.

### Any VPS or PaaS without Docker

```bash
pip install -r requirements.txt
PDFE_HOST=0.0.0.0 PDFE_DATA_DIR=/var/lib/pdf-editor \
  uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers 2 --proxy-headers
```

### Behind a reverse proxy (nginx)

```nginx
client_max_body_size 100m;          # must be >= PDFE_MAX_UPLOAD_MB
location / { proxy_pass http://127.0.0.1:8000; proxy_set_header Host $host;
             proxy_set_header X-Forwarded-Proto $scheme; proxy_read_timeout 120s; }
```

### Scaling

- **One machine:** raise `PDFE_WORKERS`. The workers share the data directory.
- **Several machines:** either mount a shared volume at `PDFE_DATA_DIR` (NFS, EFS or similar, with file-lock support) or turn on sticky sessions in the load balancer.
- **Frontend on a separate host or CDN:** serve the `static/` folder from there, set `window.PDFE_API_BASE = "https://api.example.com"` before `js/app.js` loads, and add that origin to `PDFE_CORS_ORIGINS`.

## Configuration

All settings are environment variables, and a `.env` file is also read (see `.env.example`).

| Variable | Default | Meaning |
|---|---|---|
| `PDFE_HOST` / `PDFE_PORT` | `127.0.0.1` / `8000` | Address the server listens on |
| `PDFE_WORKERS` | `1` | Number of uvicorn worker processes |
| `PDFE_DATA_DIR` | `./data` | Where documents and undo history are stored |
| `PDFE_MAX_UPLOAD_MB` | `100` | Maximum upload size |
| `PDFE_SESSION_TTL_MINUTES` | `240` | Idle documents are deleted after this many minutes |
| `PDFE_MAX_UNDO` | `50` | Undo steps kept per document |
| `PDFE_DOC_CACHE_SIZE` | `8` | Parsed documents kept in memory per worker |
| `PDFE_MAX_RENDER_SCALE` | `4` | Upper limit on page-render resolution |
| `PDFE_CORS_ORIGINS` | *(empty)* | Comma-separated allowed origins, needed only when the frontend is hosted elsewhere |

## Security notes

- Documents are stored unencrypted under `PDFE_DATA_DIR` until they expire. Put that directory on storage you trust.
- There is no user login. A document is reachable only through its random 128-bit session ID. For a public deployment, put authentication in front of the app (reverse-proxy auth, an OAuth proxy or similar) and use HTTPS.

## Licensing

This project is licensed under the **GNU AGPL-3.0** (see [LICENSE](LICENSE)). It has to be, because its PDF engine, PyMuPDF/MuPDF, is AGPL-licensed.

If you host a modified copy for other people, the AGPL requires you to offer *your* modified source code to its users too. The simplest way is a "Source code" link in the UI, like the one in this app's footer.
