---
name: mflow-memory
description: >-
  Long-term memory engine for OpenClaw agents using M-flow knowledge
  graphs. Stores conversations as structured episodic memories and
  retrieves via graph-routed search. Use when the agent needs to
  remember past conversations, recall user preferences, or maintain
  context across sessions. Requires Docker.
requiredBinaries:
  - docker
requiredEnvVars:
  - LLM_API_KEY
homepage: https://github.com/FlowElement-xinliuyuansu/m_flow
repository: https://github.com/FlowElement-xinliuyuansu/m_flow/tree/main/openclaw-skill/mflow-memory
---

# M-flow Memory

Gives your agent persistent long-term memory powered by M-flow's cognitive memory engine.

## Setup

Run once:

```bash
bash ~/.openclaw/skills/mflow-memory/scripts/setup.sh
```

This starts the M-flow MCP server and registers it with OpenClaw.

## Memory Strategy

Follow these rules for every conversation:

1. **Before answering**, silently call `search` with the user's question to check for relevant memories. If memories are found, use them as context.

2. **At the end of a conversation** that contained meaningful information (decisions, facts, preferences, plans — not greetings or small talk), call `save_interaction` with the conversation content.

3. **When the user explicitly asks you to remember something** ("remember that I'm allergic to peanuts", "note that the deadline is Friday"), call `save_interaction` immediately — do not wait until the end of the conversation.

4. **When the user says** "remember", "last time", "before", "previously", or references past events, always call `search` first.

5. **Do not store** trivial exchanges, repeated information, or content the user asks you to forget.

## Available Tools (via MCP)

After setup, these tools are automatically available:

- `save_interaction` — Store a conversation as memory (preferred for dialogue)
- `search` — Search memories by natural language query
- `query` — Ask a question and get an answer grounded in memories
- `memorize` — Build knowledge graph from previously added data
- `ingest` — One-step store + memorize (for documents)
- `list_data` — List stored datasets
- `delete` — Remove specific memories
- `memorize_status` — Check if memorization is still processing
- `prune` — Reset all memory

## Troubleshooting

```bash
# Check if M-flow is running
bash ~/.openclaw/skills/mflow-memory/scripts/status.sh

# Restart
docker restart mflow-memory

# View logs
docker logs mflow-memory --tail 20

# Complete removal
bash ~/.openclaw/skills/mflow-memory/scripts/teardown.sh
```
