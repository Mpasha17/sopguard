# syntax=docker/dockerfile:1
# Multi-stage image for the FastAPI proxy + Freshdesk webhook (default CMD)
# and the `sopguard freshdesk run` worker (override the command).

FROM python:3.12-slim AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /build

# Isolated venv so the runtime stage can copy only the install tree.
RUN python -m venv /venv
COPY pyproject.toml README.md LICENSE ./
COPY src ./src
RUN /venv/bin/pip install --upgrade pip \
    && /venv/bin/pip install ".[langgraph]"

FROM python:3.12-slim AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PATH=/venv/bin:$PATH \
    SOPGUARD_AUDIT=/data/audit.jsonl

WORKDIR /app

COPY --from=builder /venv /venv
# Freshdesk SOP lives outside the wheel; the worker and webhook look here first.
COPY policies ./policies

RUN useradd --create-home --uid 10001 --shell /usr/sbin/nologin sopguard \
    && mkdir -p /data \
    && chown -R sopguard:sopguard /app /data /venv

USER sopguard
EXPOSE 8080

# Worker containers should disable this check (nothing listens on 8080 there).
HEALTHCHECK --interval=30s --timeout=5s --start-period=20s --retries=3 \
    CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8080/health', timeout=4)"

# Factory mounts /freshdesk/webhook when FRESHDESK_DOMAIN and SOPGUARD_WEBHOOK_TOKEN are set.
CMD ["uvicorn", "sopguard.api:app_from_env", "--factory", "--host", "0.0.0.0", "--port", "8080"]
