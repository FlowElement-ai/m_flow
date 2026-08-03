
# Mflow Starter Kit
Welcome to the <a href="https://github.com/FlowElement-xinliuyuansu/m_flow">m_flow</a> Starter Repo! This repository is designed to help you get started quickly by providing a structured dataset and pre-built data pipelines using m_flow to build powerful knowledge graphs.

You can use this repo to ingest, process, and visualize data in minutes. 

By following this guide, you will:

- Load structured company and employee data
- Utilize pre-built pipelines for data processing
- Perform graph-based search and query operations
- Visualize entity relationships effortlessly on a graph

# How to Use This Repo 🛠

## Install uv if you don't have it on your system
```
pip install uv
```
## Install dependencies
```
uv sync
```

## Setup LLM
Add environment variables to `.env` file.
In case you choose to use OpenAI provider, add just the model and api_key.
```
LLM_PROVIDER=""
LLM_MODEL=""
LLM_ENDPOINT=""
LLM_API_KEY=""
LLM_API_VERSION=""

EMBEDDING_PROVIDER=""
EMBEDDING_MODEL=""
EMBEDDING_ENDPOINT=""
EMBEDDING_API_KEY=""
EMBEDDING_API_VERSION=""
```

Activate the Python environment:
```
source .venv/bin/activate
```

## Run the Default Pipeline

This script runs the memorize pipeline with default settings. It ingests text data, builds a knowledge graph, and allows you to run search queries.

```
python src/pipelines/default.py
```

## Run the Low-Level Pipeline

This script implements its own pipeline with custom ingestion task. It processes the given JSON data about companies and employees, making it searchable via a graph.

```
python src/pipelines/low_level.py
```

## Run the Custom Model Pipeline

Custom model uses custom pydantic model for graph extraction. This script categorizes programming languages as an example and visualizes relationships.

```
python src/pipelines/custom-model.py
```

## Graph Preview

Use the M-Flow web UI to visualize the knowledge graph interactively:

```bash
mflow -ui          # Launch the web console at http://localhost:3000
```

Navigate to the Knowledge Graph page to explore entity relationships and episode structures.

# What will you build with m_flow?

- Expand the dataset by adding more structured/unstructured data
- Customize the data model to fit your use case
- Use the search API to build an intelligent assistant
- Visualize knowledge graphs for better insights
