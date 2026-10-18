"""Runtime configuration, read from environment variables.

Every setting has a sensible default so the app runs locally with no setup;
override them in production (Docker, systemd, PaaS dashboard, .env file).
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

BASE_DIR = Path(__file__).resolve().parent.parent


def _load_dotenv(path: Path) -> None:
    """Tiny .env loader (KEY=VALUE lines) so no extra dependency is needed."""
    if not path.is_file():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        os.environ.setdefault(key.strip(), value.strip().strip('"').strip("'"))


_load_dotenv(BASE_DIR / ".env")


def _int(name: str, default: int) -> int:
    try:
        return int(os.environ.get(name, default))
    except ValueError:
        return default


def _list(name: str, default: str = "") -> list[str]:
    return [x.strip() for x in os.environ.get(name, default).split(",") if x.strip()]


@dataclass(frozen=True)
class Settings:
    host: str = os.environ.get("PDFE_HOST", "127.0.0.1")
    port: int = _int("PDFE_PORT", 8000)
    # Where documents + undo history are stored (one folder per session).
    data_dir: Path = Path(os.environ.get("PDFE_DATA_DIR", str(BASE_DIR / "data")))
    static_dir: Path = BASE_DIR / "static"
    max_upload_mb: int = _int("PDFE_MAX_UPLOAD_MB", 100)
    # Sessions untouched for this long are deleted from disk.
    session_ttl_minutes: int = _int("PDFE_SESSION_TTL_MINUTES", 240)
    cleanup_interval_seconds: int = _int("PDFE_CLEANUP_INTERVAL_SECONDS", 300)
    max_undo: int = _int("PDFE_MAX_UNDO", 50)
    # Parsed documents kept open in memory per worker process.
    doc_cache_size: int = _int("PDFE_DOC_CACHE_SIZE", 8)
    max_render_scale: float = float(os.environ.get("PDFE_MAX_RENDER_SCALE", "4"))
    cors_origins: list[str] = field(default_factory=lambda: _list("PDFE_CORS_ORIGINS"))
    log_level: str = os.environ.get("PDFE_LOG_LEVEL", "info")

    @property
    def max_upload_bytes(self) -> int:
        return self.max_upload_mb * 1024 * 1024


settings = Settings()
