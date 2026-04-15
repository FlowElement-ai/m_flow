<div align="center">

# M-flow

**Graph RAG finds what's similar. M-flow finds what's relevant.**

Retrieval through reasoning and association — M-flow operates like a cognitive memory system.

[m-flow.ai](https://m-flow.ai) ·
[flowelement.ai](https://flowelement.ai) ·
[Quick Start](#quick-start) ·
[Architecture](docs/RETRIEVAL_ARCHITECTURE.md) ·
[Examples](examples/) ·
[OpenClaw Skill](https://clawhub.ai/flowelement-alexunbridled/mflow-memory) ·
[Contact](mailto:contact@xinliuyuansu.com)

[![Tests](https://img.shields.io/badge/tests-963%20passed-brightgreen)](#testing)
[![Python](https://img.shields.io/badge/python-3.10–3.13-blue)](https://www.python.org/)
[![License](https://img.shields.io/badge/license-Apache%202.0-blue)](LICENSE)

</div>

---

## What is M-flow?

The fundamental shift is this: **existing systems build graphs but still retrieve by embedding distance. M-flow makes the graph the retrieval mechanism itself.**

RAG embeds chunks and ranks by vector similarity. GraphRAG goes further — it extracts entities, builds a knowledge graph, and generates community summaries. But when a query arrives, retrieval still reduces to embedding the query and matching against stored text. The graph informs what gets embedded; it does not participate in how results are scored. The retrieval step remains **similarity-driven**.

M-flow takes a different approach: the graph is the scoring engine. When a query arrives, vector search casts a wide net across multiple granularities to find entry points. Then **the graph takes over** — propagating evidence along typed, semantically weighted edges, and scoring each knowledge unit by the tightest chain of reasoning that connects it to the query.

Similar and relevant sometimes overlap, but they are fundamentally different. Consider the query **"Why did the migration fail?"**

**Traditional retrieval** — matches by surface similarity:

```mermaid
flowchart LR
    Q["Query: Why did the\nmigration fail?"] -->|"embed → cosine similarity"| C1["Chunk: Database migration\nbest practices checklist"]
    C1 -->|"✗ wrong answer"| R["keywords match,\nbut answers a\ndifferent question"]
```

**M-flow retrieval** — traces through the knowledge graph:

```mermaid
flowchart LR
    Q["Query: Why did the\nmigration fail?"] -->|search| FP["FacetPoint\nconnection pool\nexhausted at 2:47 AM"]
    FP -->|"edge:pool failure caused\nservice downtime"| F["Facet\nRedis failure\nanalysis"]
    F -->|"edge:core incident\ndetails"| E["Episode\nProduction outage\nFeb 12"]
    E -->|"✓ correct result"| R["Redis connection pool\nexhausted under peak load"]
```

> Zero keyword overlap with "migration" — found through graph path, not text similarity.

The graph finds the answer not by matching words, but by following the chain of evidence. This difference — **from distance-based ranking to path-based reasoning** — is what drives M-flow's consistent advantage across benchmarks.

**M-flow operates like a cognitive system: it captures signal at the sharpest point of detail, traces associations through structured memory, and arrives at the right answer the way human recall does.**

## How It Works

M-flow organizes knowledge into a four-level **Cone Graph** — a layered hierarchy from abstract summaries to atomic facts:

| Level | What it captures | Query example |
|-------|-----------------|---------------|
| **Episode** | A bounded semantic focus — an incident, decision process, or workflow | *"What happened with the tech stack decision?"* |
| **Facet** | One dimension of that Episode — a topical cross-section | *"What were the performance targets?"* |
| **FacetPoint** | An atomic assertion or fact derived from a Facet | *"Was the P99 target under 500ms?"* |
| **Entity** | A named thing — person, tool, metric — linked across all Episodes | *"Tell me about GPT-4o"* → surfaces all related contexts |

Retrieval is **graph-routed**: the system casts a wide net across all levels, projects hits into the knowledge graph, propagates cost along every possible path, and scores each Episode by its **tightest chain of evidence**. One strong path is enough — the way a single association triggers an entire memory.

> For the full technical deep-dive, see [Retrieval Architecture](docs/RETRIEVAL_ARCHITECTURE.md)

## Benchmarks

All systems use gpt-5-mini (answer) + gpt-4o-mini (judge). Cat 5 (adversarial) excluded from LoCoMo.

### LoCoMo-10

**Aligned (top-k = 10)**

| System | LLM-Judge | Answer LLM | Judge LLM | Top-K |
|--------|:---------:|------------|-----------|:-----:|
| **M-flow** | **81.8%** | gpt-5-mini | gpt-4o-mini | 10 |
| Cognee Cloud | 79.4% | gpt-5-mini | gpt-4o-mini | 10 |
| Zep Cloud (7e+3n) | 73.4% | gpt-5-mini | gpt-4o-mini | 10 |
| Supermemory Cloud | 64.4% | gpt-5-mini | gpt-4o-mini | 10 |

**With vendor-default retrieval budgets**

| System | LLM-Judge | Answer LLM | Judge LLM | Top-K |
|--------|:---------:|------------|-----------|:-----:|
| **M-flow** | **81.8%** | gpt-5-mini | gpt-4o-mini | 10 |
| Cognee Cloud | 79.4% | gpt-5-mini | gpt-4o-mini | 10 |
| Zep Cloud (20e+20n) | 78.4% | gpt-5-mini | gpt-4o-mini | 40 |
| Mem0ᵍ Cloud (published) | 68.5% | — | — | — |
| Mem0 Cloud (published) | 67.1% | — | — | — |
| Supermemory Cloud | 64.4% | gpt-5-mini | gpt-4o-mini | 10 |
| Mem0 Cloud (tested) | 50.4% | gpt-5-mini | gpt-4o-mini | 30 |

### LongMemEval

First 100 questions, top-k=10.

| System | LLM-Judge | Temporal (60) | Multi-session (40) |
|--------|:---------:|:-------------:|:------------------:|
| **M-flow** | **89%** | **93%** | **82%** |
| Supermemory Cloud | 74% | 78% | 68% |
| Mem0 Cloud | 71% | 77% | 63% |
| Zep Cloud | 61% | 82% | 30% |
| Cognee | 57% | 67% | 43% |

Per-category breakdowns, reproduction scripts, raw data, and methodology for all systems: [mflow-benchmarks](https://github.com/FlowElement-ai/mflow-benchmarks)

## Features

| | |
|---------|-------------|
| **Episodic + Procedural memory** | Hierarchical recall for facts and step-by-step knowledge |
| **5 retrieval modes** | Episodic, Procedural, Triplet Completion, Lexical, Cypher |
| **50+ file formats** | PDFs, DOCX, HTML, Markdown, images, audio, and more |
| **Multi-DB support** | LanceDB, Neo4j, PostgreSQL/pgvector, ChromaDB, KùzuDB, Pinecone |
| **LLM-agnostic** | OpenAI, Anthropic, Mistral, Groq, Ollama, LLaMA-Index, LangChain |
| **Precise summarization** | Preserves all factual details (dates, numbers, names) at the cost of lower compression — RAG context will be longer but more accurate |
| **MCP server** | Expose memory as Model Context Protocol tools for any IDE |
| **CLI & Web UI** | Interactive console, knowledge graph visualization, config wizard |

> **Retrieval modes**: **Episodic** is the primary retrieval mode — it uses graph-routed Bundle Search for best accuracy and is used in all benchmarks. **Triplet Completion** is a simpler vector-based mode suited for customization and secondary development. See [Retrieval Architecture](docs/RETRIEVAL_ARCHITECTURE.md) for details.

## Quick Start

### One-Command Setup (Docker)

```bash
git clone https://github.com/FlowElement-ai/m_flow.git && cd m_flow
./quickstart.sh
```

The script checks your environment, configures API keys interactively, and starts the full stack (backend + frontend). On Windows, use `.\quickstart.ps1`.

### Install via pip

```bash
pip install mflow-ai         # or: uv pip install mflow-ai
export LLM_API_KEY="sk-..."
```

### Install from Source

```bash
git clone https://github.com/FlowElement-ai/m_flow.git && cd m_flow
pip install -e .             # editable install for development
```

### Run

```python
import asyncio
import m_flow


async def main():
    await m_flow.add("M-flow builds persistent memory for AI agents.")
    await m_flow.memorize()

    results = await m_flow.search("How does M-flow work?")
    for r in results:
        print(r)


asyncio.run(main())
```

### CLI

```bash
mflow add "M-flow builds persistent memory for AI agents."
mflow memorize
mflow search "How does M-flow work?"
mflow -ui          # Launch the local web console
```

## Architecture Overview

```
┌───────────────┐     ┌───────────────┐     ┌───────────────┐
│  Data Input   │────▶│    Extract    │────▶│   Memorize    │
│  (50+ formats)│     │  (chunking,   │     │  (KG build,   │
│               │     │   parsing)    │     │  embeddings)  │
└───────────────┘     └───────────────┘     └───────┬───────┘
                                                    │
                      ┌───────────────┐     ┌───────▼───────┐
                      │    Search     │◀────│     Load      │
                      │  (graph-routed│     │   (graph +    │
                      │  bundle search│     │  vector DB)   │
                      └───────────────┘     └───────────────┘
```

## Project Layout

```
m_flow/              Core Python library & API
├── api/             FastAPI routers (add, memorize, search, …)
├── cli/             Command-line interface (`mflow`)
├── adapters/        DB adapters (graph, vector, cache)
├── core/            Domain models (Episode, Facet, FacetPoint, …)
├── memory/          Memory processing (episodic, procedural)
├── retrieval/       Search & retrieval algorithms
├── pipeline/        Composable pipeline tasks & orchestration
├── auth/            Authentication & multi-tenancy
├── shared/          Logging, settings, cross-cutting utilities
├── tests/           Unit & integration tests
└── api/v1/playground/  Face-aware interactive chat (Playground)

m_flow-frontend/     Next.js web console
m_flow-mcp/          Model Context Protocol server
mflow_workers/       Distributed execution helpers (Modal, workers)
examples/            Runnable example scripts
docs/                Architecture & design documents
```

## Development

```bash
git clone https://github.com/FlowElement-ai/m_flow.git && cd m_flow
uv sync --dev --all-extras --reinstall

# Test
PYTHONPATH=. uv run pytest m_flow/tests/unit/ -v

# Lint
uv run ruff check . && uv run ruff format .
```

See [`CONTRIBUTING.md`](CONTRIBUTING.md) for the full contributor guide.

## Deployment

### Docker

```bash
docker compose up                       # Backend only
docker compose --profile ui up          # Backend + frontend
docker compose --profile neo4j up       # Backend + Neo4j
docker compose --profile postgres up    # Backend + PostgreSQL + PGVector
```

### Playground with Face Recognition

The Playground provides interactive multi-person conversations with face-aware memory.
It requires [fanjing-face-recognition](https://github.com/FlowElement-ai/fanjing-face-recognition) as a companion service.

```bash
# 1. Clone fanjing-face-recognition next to m_flow
git clone https://github.com/FlowElement-ai/fanjing-face-recognition.git ../fanjing-face-recognition

# 2. Download face models
cd ../fanjing-face-recognition
python scripts/download_model.py        # det_10g.onnx  (detection)
python scripts/download_arcface.py      # w600k_r50.onnx (embedding)
# Also download face_landmarker.task from MediaPipe if needed

# 3. Generate a shared API key and add to .env
cd ../m_flow
python -c "import secrets; print('FACE_API_KEY=' + secrets.token_urlsafe(32))" >> .env

# 4. Launch everything
docker compose --profile ui --profile playground up --build -d
```

The Playground UI is available at `http://localhost:3000` → Playground tab.
The face recognition service runs at `http://localhost:5001`.

**Running without Docker** (local development):

```bash
# Terminal 1: face recognition
cd fanjing-face-recognition
export FACE_API_KEY="your-shared-key"
python run_web_v2.py --host 0.0.0.0 --port 5001 --no-browser

# Terminal 2: M-flow backend
cd m_flow
export FACE_API_KEY="your-shared-key"
python -m uvicorn m_flow.api.server:app --host 0.0.0.0 --port 8000
```

> **Note:** When M-flow runs inside Docker and fanjing runs on the host,
> the backend automatically translates `localhost` → `host.docker.internal`.
> No manual URL configuration is needed.

### MCP Server

```bash
cd m_flow-mcp
uv sync --dev --all-extras
uv run python src/server.py --transport sse
```

## Testing

```bash
PYTHONPATH=. pytest m_flow/tests/unit/ -v        # ~963 test cases
PYTHONPATH=. pytest m_flow/tests/integration/ -v  # Needs .env with API keys
```

## Contributing

We welcome contributions! Please see [CONTRIBUTING.md](CONTRIBUTING.md) for guidelines, and our [Code of Conduct](CODE_OF_CONDUCT.md) for community standards.

## License

M-flow is licensed under the [Apache License 2.0](LICENSE).

```
Copyright 2026 Junting Hua

Licensed under the Apache License, Version 2.0.
You may obtain a copy of the License at http://www.apache.org/licenses/LICENSE-2.0
```
