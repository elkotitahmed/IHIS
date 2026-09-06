# iHIS — production container (Hugging Face Docker Space, Cloud Run, Render,
# any Docker host). Listens on $PORT (default 7860, the Hugging Face port).
FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    PORT=7860

# libglib/libgomp are needed by opencv-headless and torch; curl for the healthcheck.
RUN apt-get update \
    && apt-get install -y --no-install-recommends libglib2.0-0 libgomp1 curl \
    && rm -rf /var/lib/apt/lists/*

# Hugging Face runs containers as uid 1000; do the same everywhere.
RUN useradd -m -u 1000 ihis
WORKDIR /app

# CPU-only torch first (the default index pulls multi-GB CUDA wheels).
COPY requirements.txt .
RUN pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchvision>=0.17" \
    && pip install -r requirements.txt \
    && pip install "huggingface_hub>=0.24"

COPY --chown=ihis:ihis . .
RUN mkdir -p /app/var/uploads /app/backup/backups /app/logs /app/instance \
    && chown -R ihis:ihis /app
USER ihis

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health/live" || exit 1

CMD ["/bin/sh", "deployment/entrypoint.sh"]
