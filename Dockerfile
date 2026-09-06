# iHIS — production container (Hugging Face Docker Space, Render, Cloud Run,
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

# IHIS_ML=1 (default): full image with the image-AI stack (CPU-only torch first,
# because the default index pulls multi-GB CUDA wheels).
# IHIS_ML=0: light image (~400 MB) for 512 MB hosts such as Render's free tier —
# image-AI pages degrade gracefully; Copilot, radiology rules/classifier and
# Gemini all work.
ARG IHIS_ML=1
COPY requirements.txt requirements-dev.txt ./
RUN if [ "$IHIS_ML" = "1" ]; then \
        pip install --index-url https://download.pytorch.org/whl/cpu "torch>=2.2" "torchvision>=0.17" \
        && pip install -r requirements.txt ; \
    else \
        grep -viE "pytest" requirements-dev.txt > /tmp/req-light.txt \
        && pip install -r /tmp/req-light.txt gunicorn==23.0.0 "scikit-learn>=1.3" "pandas>=2.0" "joblib>=1.3" "a2wsgi>=1.10" ; \
    fi \
    && pip install "huggingface_hub>=0.24"

COPY --chown=ihis:ihis . .
RUN mkdir -p /app/var/uploads /app/backup/backups /app/logs /app/instance \
    && chown -R ihis:ihis /app
USER ihis

EXPOSE 7860
HEALTHCHECK --interval=60s --timeout=10s --start-period=180s --retries=3 \
    CMD curl -fsS "http://127.0.0.1:${PORT}/health/live" || exit 1

CMD ["/bin/sh", "deployment/entrypoint.sh"]
