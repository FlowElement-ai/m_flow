# M-flow MCP Server Image
# Model Context Protocol server for M-flow knowledge graphs

FROM python:3.14-slim AS builder

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential git curl libpq-dev && \
    rm -rf /var/lib/apt/lists/*

WORKDIR /srv/mcp

# Copy ALL source first, then install (editable install needs source present)
COPY m_flow-mcp/pyproject.toml ./pyproject.toml
COPY m_flow-mcp/src/ ./src/

# Install main backend from the parent context so m_flow is available locally
COPY pyproject.toml /tmp/m_flow/pyproject.toml
COPY README.md /tmp/m_flow/README.md
COPY m_flow/ /tmp/m_flow/m_flow/
RUN pip install --no-cache-dir /tmp/m_flow && rm -rf /tmp/m_flow

# Install MCP package (deps already satisfied by m_flow above)
RUN pip install --no-cache-dir ".[all]"

# ── Runtime ──────────────────────────────────────────────────────────────────
FROM python:3.14-slim AS runtime

WORKDIR /srv/mcp

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin
COPY --from=builder /srv/mcp /srv/mcp

# Copy m_flow source for direct-mode (non-API) operation
COPY m_flow/ /srv/mcp/m_flow/
COPY alembic.ini /srv/mcp/alembic.ini
COPY alembic/ /srv/mcp/alembic/

ENV PYTHONPATH=/srv/mcp \
    PYTHONUNBUFFERED=1

EXPOSE 8000

RUN apt-get update && apt-get install -y --no-install-recommends curl && \
    rm -rf /var/lib/apt/lists/*

COPY m_flow-mcp/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
    CMD curl -sf http://localhost:8000/health || exit 1

ENTRYPOINT ["/entrypoint.sh"]
CMD ["serve"]
