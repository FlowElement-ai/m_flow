# =============================================================================
# M-flow Backend — Production Container
# Two-phase build: heavy builder → lightweight runner
# =============================================================================

# Phase A ── Build environment with uv package manager
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS builder

ARG DEBUG=false
ARG UV_EXTRAS="debug deploy api postgres neo4j llama-index ollama mistral groq anthropic langchain"

WORKDIR /opt/m_flow

ENV UV_LINK_MODE=copy \
    UV_HTTP_TIMEOUT=300 \
    DEBUG=${DEBUG}

# OS-level toolchain required by compiled wheels (psycopg2, etc.)
RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends \
        build-essential gcc clang cmake git curl libpq-dev; \
    rm -rf /var/lib/apt/lists/*

# ── Layer 1: Python dependencies (changes rarely) ───────────────────────────
COPY README.md pyproject.toml uv.lock entrypoint.sh ./

# Build the --extra flags dynamically from the ARG
RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    extra_flags=""; \
    for e in ${UV_EXTRAS}; do extra_flags="${extra_flags} --extra ${e}"; done; \
    uv sync ${extra_flags} --frozen --no-install-project --no-dev --no-editable

# ── Layer 2: Alembic migrations ─────────────────────────────────────────────
COPY alembic.ini ./alembic.ini
COPY alembic/ ./alembic/

# ── Layer 3: Application code (changes most often) ──────────────────────────
COPY m_flow/       ./m_flow/
COPY mflow_workers/  ./mflow_workers/
COPY coreference/  ./coreference/

# Remove local file dependencies that uv can't handle with relative paths
# The coreference package will be installed separately via pip
RUN sed -i '/chinese-coref.*file:\.\/coreference/d' pyproject.toml

RUN --mount=type=cache,target=/root/.cache/uv \
    set -eux; \
    extra_flags=""; \
    for e in ${UV_EXTRAS}; do extra_flags="${extra_flags} --extra ${e}"; done; \
    uv sync ${extra_flags} --frozen --no-dev --no-editable || \
    uv sync ${extra_flags} --no-dev --no-editable

# Install coreference package with all dependencies into the virtual environment
# This restores full coreference functionality (Chinese/English pronoun resolution)
RUN --mount=type=cache,target=/root/.cache/uv \
    uv pip install --no-cache ./coreference/

# ─────────────────────────────────────────────────────────────────────────────
# Phase B ── Slim runtime (no compiler toolchain, no uv)
# ─────────────────────────────────────────────────────────────────────────────
FROM python:3.14-slim-bookworm AS runner

LABEL maintainer="M-flow Team" \
      description="M-flow backend API server" \
      org.opencontainers.image.source="https://github.com/FlowElement-ai/m_flow"

RUN set -eux; \
    apt-get update; \
    apt-get install -y --no-install-recommends libpq5 curl; \
    rm -rf /var/lib/apt/lists/*

WORKDIR /opt/m_flow

COPY --from=builder /opt/m_flow /opt/m_flow
RUN chmod +x /opt/m_flow/entrypoint.sh

# Runtime environment
ENV PATH="/opt/m_flow/.venv/bin:${PATH}" \
    PYTHONPATH=/opt/m_flow \
    PYTHONUNBUFFERED=1

EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
    CMD curl -f http://localhost:8000/health || exit 1

ENTRYPOINT ["/opt/m_flow/entrypoint.sh"]
