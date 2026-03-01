# ---- Stage 1: Resolve Python dependencies ----
FROM python:3.11-slim AS builder

WORKDIR /build
COPY pyproject.toml poetry.lock ./

RUN pip install --no-cache-dir poetry==2.3.2 \
    && poetry config virtualenvs.in-project true \
    && poetry install --only=main --no-interaction --no-ansi --no-root

# ---- Stage 2: Download MediaMTX binary ----
FROM python:3.11-slim AS mediamtx-dl

ARG MEDIAMTX_VERSION=v1.11.3
ARG TARGETARCH

RUN apt-get update && apt-get install -y --no-install-recommends curl ca-certificates \
    && rm -rf /var/lib/apt/lists/*

RUN ARCH=$(case "${TARGETARCH}" in \
        amd64) echo "amd64" ;; \
        arm64) echo "arm64v8" ;; \
        *) echo "amd64" ;; \
    esac) \
    && curl -fSL --retry 3 -o /tmp/mediamtx.tar.gz \
       "https://github.com/bluenviron/mediamtx/releases/download/${MEDIAMTX_VERSION}/mediamtx_${MEDIAMTX_VERSION}_linux_${ARCH}.tar.gz" \
    && mkdir -p /opt/mediamtx \
    && tar -xzf /tmp/mediamtx.tar.gz -C /opt/mediamtx \
    && chmod +x /opt/mediamtx/mediamtx \
    && rm /tmp/mediamtx.tar.gz

# ---- Stage 3: Runtime ----
FROM python:3.11-slim AS runtime

RUN apt-get update \
    && apt-get install -y --no-install-recommends \
       libglib2.0-0 \
       libsm6 \
       libxext6 \
       libxrender1 \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /app

# Python virtualenv from builder
COPY --from=builder /build/.venv /app/.venv

# MediaMTX binary
COPY --from=mediamtx-dl /opt/mediamtx/mediamtx /usr/local/bin/mediamtx

# Shared protocol code (from additional build context)
COPY --from=shared . /app/services/shared/

# Backend application code (from primary build context)
COPY . /app/services/backend/

ENV PATH="/app/.venv/bin:$PATH"
ENV PYTHONPATH="/app/services"
ENV PYTHONUNBUFFERED=1

# Default config for container
ENV CC_BACKEND_HOST=0.0.0.0
ENV CC_BACKEND_PORT=8000
ENV CC_BACKEND_MEDIAMTX_ENABLED=true
ENV CC_BACKEND_MEDIAMTX_BINARY=mediamtx
ENV CC_BACKEND_RTSP_CONSUMER_ENABLED=true

# 8000=FastAPI  8890=SRT(UDP)  8554=RTSP  8889=WebRTC  8189=ICE(UDP)
EXPOSE 8000 8554 8889 8890/udp 8189/udp

HEALTHCHECK --interval=15s --timeout=5s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8000/health')" || exit 1

CMD ["python", "-m", "uvicorn", "backend.main:app", "--host", "0.0.0.0", "--port", "8000"]
