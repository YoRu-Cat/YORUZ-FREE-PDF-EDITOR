"""Local launcher: `python run.py` (settings come from env vars / .env)."""

import os
import webbrowser

import uvicorn

from app.config import settings

if __name__ == "__main__":
    url = f"http://{'localhost' if settings.host in ('0.0.0.0', '127.0.0.1') else settings.host}:{settings.port}"
    print(f"\n  PDF Editor running at {url}\n")
    if os.environ.get("PDFE_OPEN_BROWSER", "1") == "1":
        webbrowser.open(url)
    uvicorn.run(
        "app.main:app",
        host=settings.host,
        port=settings.port,
        log_level=settings.log_level,
        workers=int(os.environ.get("PDFE_WORKERS", "1")),
    )
