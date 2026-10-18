FROM python:3.12-slim

# Fonts used when edited text needs a substitute for a font that is not embedded:
#  liberation = Arial/Times/Courier metrics, crosextra = Calibri/Cambria metrics,
#  dejavu + noto = broad Unicode coverage.
RUN apt-get update \
 && apt-get install -y --no-install-recommends \
      fonts-liberation fonts-crosextra-carlito fonts-crosextra-caladea \
      fonts-dejavu-core fonts-noto-core \
 && rm -rf /var/lib/apt/lists/*

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PDFE_HOST=0.0.0.0 \
    PDFE_PORT=8000 \
    PDFE_DATA_DIR=/data \
    PDFE_WORKERS=2

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY app ./app
COPY static ./static

RUN useradd --create-home --uid 10001 pdfe \
 && mkdir -p /data && chown pdfe:pdfe /data
USER pdfe
# /data holds sessions; mount a volume there to keep them across redeploys
# (no VOLUME instruction: some platforms, e.g. Railway, reject it).
EXPOSE 8000

# Hosting platforms (Render, Railway, Fly, Koyeb, Cloud Run…) inject $PORT; fall back to PDFE_PORT.
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
  CMD python -c "import urllib.request,os; urllib.request.urlopen('http://127.0.0.1:%s/api/health' % os.environ.get('PORT', os.environ.get('PDFE_PORT', '8000')), timeout=4)"

CMD ["sh", "-c", "exec uvicorn app.main:app --host \"$PDFE_HOST\" --port \"${PORT:-$PDFE_PORT}\" --workers \"$PDFE_WORKERS\" --proxy-headers --forwarded-allow-ips='*'"]
