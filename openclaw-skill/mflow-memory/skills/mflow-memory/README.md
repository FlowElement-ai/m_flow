# M-flow Memory for OpenClaw

Give your OpenClaw agent persistent long-term memory.

M-flow stores conversations as structured knowledge graphs (Episodes, Facets, Entities) and retrieves them via graph-routed search — not just vector similarity.

## Install

```bash
clawhub install mflow-memory
```

## Setup

```bash
bash ~/.openclaw/skills/mflow-memory/scripts/setup.sh
```

You'll need:
- Docker ([install](https://docs.docker.com/get-docker/))
- An OpenAI API key ([get one](https://platform.openai.com/api-keys))

Setup takes ~1 minute. After that, restart OpenClaw — your agent automatically gains memory tools via MCP.

## What it does

- **Remembers** conversations, decisions, preferences, and facts
- **Recalls** relevant memories when you ask about past events
- **Persists** across restarts (data stored in Docker volume)
- **11 MCP tools** available to your agent (search, memorize, query, delete, etc.)

## Requirements

- Docker Desktop running
- ~1GB disk space (Docker image)
- OpenAI API key (for LLM-based knowledge extraction)

## Links

- [M-flow GitHub](https://github.com/FlowElement-xinliuyuansu/m_flow)
- [M-flow Documentation](https://docs.m-flow.ai)
- [LoCoMo Benchmark: 81.8%](https://github.com/FlowElement-xinliuyuansu/mflow-benchmarks)
