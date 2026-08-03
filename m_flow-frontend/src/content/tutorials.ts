/**
 * Tutorial Content Data
 *
 * Contains all tutorial content, code examples, and environment variable
 * definitions for the Getting Started section of the Setup page.
 *
 * Content is organized into:
 * 1. Tutorials - Step-by-step guides
 * 2. Code Examples - Runnable code snippets
 * 3. Environment Variables - .env configuration reference
 * 4. Quick Actions - Navigation shortcuts
 */

import type {
  TutorialOption,
  CodeExample,
  EnvSection,
  QuickAction,
} from "@/types/setup";

// ============================================================================
// Tutorials
// ============================================================================

export const TUTORIALS: TutorialOption[] = [
  {
    id: "first-memory",
    title: "Add Your First Memory",
    description: "Learn how to ingest a document and create your first knowledge graph entries",
    durationMinutes: 5,
    difficulty: "beginner",
    isInteractive: true,
    category: "quickstart",
    icon: "Sparkles",
    docLink: "https://docs.m-flow.ai/docs/getting-started/quickstart",
  },
  {
    id: "episodic-search",
    title: "Episodic Search",
    description: "Search your knowledge graph using natural language queries",
    durationMinutes: 5,
    difficulty: "beginner",
    isInteractive: true,
    category: "quickstart",
    icon: "Search",
    docLink: "https://docs.m-flow.ai/docs/guides/searching",
  },
  {
    id: "python-sdk",
    title: "Python SDK Guide",
    description: "Integrate M-Flow into your Python applications with the official SDK",
    durationMinutes: 10,
    difficulty: "beginner",
    isInteractive: false,
    category: "api",
    icon: "Code",
    docLink: "https://docs.m-flow.ai/docs/reference/python-sdk",
  },
  {
    id: "rest-api",
    title: "REST API Reference",
    description: "Complete reference for all REST API endpoints",
    durationMinutes: 15,
    difficulty: "intermediate",
    isInteractive: false,
    category: "api",
    icon: "Globe",
    docLink: "https://docs.m-flow.ai/docs/reference/rest-api",
  },
  {
    id: "llm-configuration",
    title: "Configure LLM Provider",
    description: "Set up OpenAI, Anthropic, Ollama, or other LLM providers",
    durationMinutes: 5,
    difficulty: "beginner",
    isInteractive: false,
    category: "configuration",
    icon: "Settings",
    docLink: "https://docs.m-flow.ai/docs/reference/environment",
  },
  {
    id: "database-setup",
    title: "Database Configuration",
    description: "Configure vector, graph, and relational databases",
    durationMinutes: 10,
    difficulty: "intermediate",
    isInteractive: false,
    category: "configuration",
    icon: "Database",
    docLink: "https://docs.m-flow.ai/docs/reference/environment",
  },
  {
    id: "knowledge-graph",
    title: "Understanding Knowledge Graphs",
    description: "Learn about Episodes, Facets, FacetPoints, and Concepts",
    durationMinutes: 15,
    difficulty: "intermediate",
    isInteractive: false,
    category: "advanced",
    icon: "GitBranch",
    docLink: "https://docs.m-flow.ai/docs/concepts/graph-structure",
  },
  {
    id: "retrieval-modes",
    title: "Retrieval Modes Deep Dive",
    description: "Master TRIPLET_COMPLETION, EPISODIC, PROCEDURAL, and more",
    durationMinutes: 20,
    difficulty: "advanced",
    isInteractive: false,
    category: "advanced",
    icon: "Layers",
    docLink: "https://docs.m-flow.ai/docs/concepts/retrieval-modes",
  },
];

// ============================================================================
// Code Examples
// ============================================================================

export const CODE_EXAMPLES: CodeExample[] = [
  // Python Examples
  {
    id: "python-add",
    title: "Add Memory (Python)",
    description: "Ingest a document into the knowledge graph",
    language: "python",
    code: `import m_flow

# Add a document to the knowledge graph
result = await m_flow.add(
    data="M-Flow is an open-source framework that converts "
         "unstructured data into a persistent knowledge graph.",
    dataset_name="main_dataset",
)

print(f"Status: {result.status}")
print(f"Dataset: {result.dataset_name}")`,
    showLineNumbers: true,
    highlightLines: [4, 5, 6, 7],
  },
  {
    id: "python-search",
    title: "Search Memory (Python)",
    description: "Query the knowledge graph using natural language",
    language: "python",
    code: `import asyncio
import m_flow

async def search_example():
    results = await m_flow.search(
        query_text="How does M-Flow work?",
        query_type="EPISODIC",
        top_k=10,
    )

    for result in results:
        print(result.search_result)

asyncio.run(search_example())`,
    showLineNumbers: true,
    highlightLines: [5, 6, 7, 8],
  },
  {
    id: "python-memorize",
    title: "Full Memorize Pipeline (Python)",
    description: "Run the complete ingestion pipeline on documents",
    language: "python",
    code: `import m_flow

# Step 1: Add documents to a dataset
await m_flow.add(
    data="path/to/document.pdf",
    dataset_name="my_dataset",
)

# Step 2: Run the full memorize pipeline on the dataset
results = await m_flow.memorize(
    datasets="my_dataset",
)

for dataset_id, info in results.items():
    print(f"Dataset: {info.dataset_name}")
    print(f"Status: {info.status}")`,
    showLineNumbers: true,
  },
  // Bash/cURL Examples
  {
    id: "curl-health",
    title: "Health Check (cURL)",
    description: "Check system health via REST API",
    language: "bash",
    code: `# Basic health check
curl http://localhost:8000/health

# Detailed health with all probes
curl http://localhost:8000/health/detailed`,
    output: `{
  "status": "ready",
  "health": "up",
  "version": "0.5.x"
}`,
  },
  {
    id: "curl-add",
    title: "Add Memory (cURL)",
    description: "Ingest data via REST API using file upload",
    language: "bash",
    code: `# Upload a text file
curl -X POST http://localhost:8000/api/v1/add \\
  -F "data=@document.txt" \\
  -F "datasetName=main_dataset"

# Or create a file from string and upload
echo "Your document content here" > temp.txt
curl -X POST http://localhost:8000/api/v1/add \\
  -F "data=@temp.txt" \\
  -F "datasetName=main_dataset"`,
  },
  {
    id: "curl-search",
    title: "Search Memory (cURL)",
    description: "Query the knowledge graph via REST API",
    language: "bash",
    code: `curl -X POST http://localhost:8000/api/v1/search \\
  -H "Content-Type: application/json" \\
  -d '{
    "query": "How does M-Flow work?",
    "recall_mode": "EPISODIC",
    "top_k": 10
  }'`,
  },
  // TypeScript Examples
  {
    id: "typescript-fetch",
    title: "API Client (TypeScript)",
    description: "Use fetch to interact with M-Flow API",
    language: "typescript",
    code: `const API_BASE = "http://localhost:8000";

// Add memory (file upload)
async function addMemory(content: string, datasetName: string) {
  // Create a file from the content
  const blob = new Blob([content], { type: "text/plain" });
  const file = new File([blob], "input.txt", { type: "text/plain" });
  
  const formData = new FormData();
  formData.append("data", file);
  formData.append("datasetName", datasetName);
  
  const response = await fetch(\`\${API_BASE}/api/v1/add\`, {
    method: "POST",
    body: formData,  // Note: Don't set Content-Type header for FormData
  });
  return response.json();
}

// Search memory
async function searchMemory(query: string, topK = 10) {
  const response = await fetch(\`\${API_BASE}/api/v1/search\`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({
      query: query,           // Note: field is "query", not "query_text"
      recall_mode: "EPISODIC", // Note: field is "recall_mode", not "query_type"
      top_k: topK,
    }),
  });
  return response.json();
}`,
    showLineNumbers: true,
  },
];

// ============================================================================
// Environment Variables
// ============================================================================

export const ENV_SECTIONS: EnvSection[] = [
  {
    id: "llm",
    title: "LLM Configuration",
    description: "Language model settings for knowledge extraction and search",
    variables: [
      {
        key: "LLM_PROVIDER",
        description: "LLM provider (openai, anthropic, ollama, gemini, mistral, bedrock)",
        example: "openai",
        required: false,
        service: "llm_provider",
        defaultValue: "openai",
      },
      {
        key: "LLM_MODEL",
        description: "Model name",
        example: "gpt-5-nano",
        required: false,
        service: "llm_provider",
        defaultValue: "gpt-5-nano",
      },
      {
        key: "LLM_API_KEY",
        description: "LLM API key (used for all providers)",
        example: "sk-...",
        required: false,
        service: "llm_provider",
        sensitive: true,
      },
      {
        key: "LLM_ENDPOINT",
        description: "Custom endpoint URL (for Ollama or custom deployments)",
        example: "http://localhost:11434",
        required: false,
        service: "llm_provider",
      },
      {
        key: "LLM_TEMPERATURE",
        description: "Generation temperature (0.0 - 2.0)",
        example: "0.0",
        required: false,
        service: "llm_provider",
        defaultValue: "0.0",
      },
    ],
  },
  {
    id: "embedding",
    title: "Embedding Configuration",
    description: "Embedding model settings for vector search",
    variables: [
      {
        key: "EMBEDDING_PROVIDER",
        description: "Embedding provider",
        example: "openai",
        required: false,
        service: "embedding_service",
        defaultValue: "openai",
      },
      {
        key: "EMBEDDING_MODEL",
        description: "Embedding model name",
        example: "openai/text-embedding-3-large",
        required: false,
        service: "embedding_service",
        defaultValue: "openai/text-embedding-3-large",
      },
      {
        key: "EMBEDDING_DIMENSIONS",
        description: "Embedding vector dimensions",
        example: "3072",
        required: false,
        service: "embedding_service",
        defaultValue: "3072",
      },
    ],
  },
  {
    id: "vector_db",
    title: "Vector Database",
    description: "Vector store configuration for embeddings",
    variables: [
      {
        key: "VECTOR_DB_PROVIDER",
        description: "Vector database provider",
        example: "lancedb",
        required: false,
        service: "vector_db",
        defaultValue: "lancedb",
      },
      {
        key: "VECTOR_DB_URL",
        description: "Vector database connection URL",
        example: "http://localhost:6333",
        required: false,
        service: "vector_db",
      },
      {
        key: "VECTOR_DB_API_KEY",
        description: "Vector database API key",
        example: "your-api-key",
        required: false,
        service: "vector_db",
        sensitive: true,
      },
    ],
  },
  {
    id: "graph_db",
    title: "Graph Database",
    description: "Knowledge graph storage configuration",
    variables: [
      {
        key: "GRAPH_DATABASE_PROVIDER",
        description: "Graph database provider",
        example: "kuzu",
        required: false,
        service: "graph_db",
        defaultValue: "kuzu",
      },
      {
        key: "GRAPH_DATABASE_URL",
        description: "Graph database URL (for Neo4j)",
        example: "bolt://localhost:7687",
        required: false,
        service: "graph_db",
      },
      {
        key: "GRAPH_DATABASE_USERNAME",
        description: "Graph database username",
        example: "neo4j",
        required: false,
        service: "graph_db",
      },
      {
        key: "GRAPH_DATABASE_PASSWORD",
        description: "Graph database password",
        example: "password",
        required: false,
        service: "graph_db",
        sensitive: true,
      },
    ],
  },
  {
    id: "relational_db",
    title: "Relational Database",
    description: "Metadata and state storage",
    variables: [
      {
        key: "DB_PROVIDER",
        description: "Relational database provider (sqlite or postgresql)",
        example: "sqlite",
        required: false,
        service: "relational_db",
        defaultValue: "sqlite",
      },
      {
        key: "DB_PATH",
        description: "Directory containing database files",
        example: "./data/databases",
        required: false,
        service: "relational_db",
      },
      {
        key: "DB_NAME",
        description: "Database name",
        example: "mflow_store",
        required: false,
        service: "relational_db",
        defaultValue: "mflow_store",
      },
      {
        key: "DB_HOST",
        description: "Database server hostname (for PostgreSQL)",
        example: "localhost",
        required: false,
        service: "relational_db",
      },
      {
        key: "DB_PORT",
        description: "Database server port (for PostgreSQL)",
        example: "5432",
        required: false,
        service: "relational_db",
      },
      {
        key: "DB_USERNAME",
        description: "Database authentication username",
        example: "postgres",
        required: false,
        service: "relational_db",
      },
      {
        key: "DB_PASSWORD",
        description: "Database authentication password",
        example: "password",
        required: false,
        service: "relational_db",
        sensitive: true,
      },
    ],
  },
  {
    id: "storage",
    title: "File Storage",
    description: "Document and asset storage",
    variables: [
      {
        key: "DATA_ROOT_DIRECTORY",
        description: "Primary storage path for user data artifacts (can be local path or s3://bucket)",
        example: "./data",
        required: false,
        service: "file_storage",
        defaultValue: "./data",
      },
      {
        key: "SYSTEM_ROOT_DIRECTORY",
        description: "Internal system state directory",
        example: "./data/system",
        required: false,
        service: "file_storage",
      },
      {
        key: "CACHE_ROOT_DIRECTORY",
        description: "Transient cache objects directory",
        example: "./data/cache",
        required: false,
        service: "file_storage",
      },
      {
        key: "S3_BUCKET_NAME",
        description: "S3 bucket name (when using S3 storage)",
        example: "my-mflow-bucket",
        required: false,
        service: "file_storage",
      },
    ],
  },
];

// ============================================================================
// Quick Actions
// ============================================================================

export const QUICK_ACTIONS: QuickAction[] = [
  {
    id: "memorize",
    title: "Ingest Documents",
    description: "Add documents to your knowledge graph",
    icon: "Upload",
    href: "/memorize",
  },
  {
    id: "search",
    title: "Search Knowledge",
    description: "Query your knowledge graph",
    icon: "Search",
    href: "/retrieve",
  },
  {
    id: "visualize",
    title: "Explore Graph",
    description: "Visualize your knowledge graph",
    icon: "GitBranch",
    href: "/memories",
  },
  {
    id: "api-docs",
    title: "API Documentation",
    description: "Full API reference",
    icon: "Book",
    href: "https://docs.m-flow.ai",
    external: true,
  },
  {
    id: "github",
    title: "GitHub Repository",
    description: "Source code and issues",
    icon: "Github",
    href: "https://github.com/FlowElement-xinliuyuansu/m_flow",
    external: true,
  },
];

// ============================================================================
// Category Metadata
// ============================================================================

export const TUTORIAL_CATEGORIES = {
  quickstart: {
    title: "Quick Start",
    description: "Get up and running in minutes",
    icon: "Zap",
  },
  api: {
    title: "API & SDK",
    description: "Integration guides",
    icon: "Code",
  },
  configuration: {
    title: "Configuration",
    description: "System setup guides",
    icon: "Settings",
  },
  advanced: {
    title: "Advanced Topics",
    description: "Deep dive into M-Flow",
    icon: "GraduationCap",
  },
} as const;

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Get tutorials by category
 */
export function getTutorialsByCategory(category: string): TutorialOption[] {
  return TUTORIALS.filter((t) => t.category === category);
}

/**
 * Get code examples by language
 */
export function getCodeExamplesByLanguage(language: string): CodeExample[] {
  return CODE_EXAMPLES.filter((e) => e.language === language);
}

/**
 * Get environment variables by service
 */
export function getEnvVariablesByService(service: string): EnvSection[] {
  return ENV_SECTIONS.filter((section) =>
    section.variables.some((v) => v.service === service)
  );
}

/**
 * Generate .env template content
 */
export function generateEnvTemplate(sectionIds?: string[]): string {
  const sections = sectionIds
    ? ENV_SECTIONS.filter((s) => sectionIds.includes(s.id))
    : ENV_SECTIONS;

  return sections
    .map((section) => {
      const header = `# ${section.title}\n# ${section.description}`;
      const vars = section.variables
        .map((v) => {
          const comment = v.required ? "" : "# ";
          const value = v.sensitive ? "" : v.example;
          return `${comment}${v.key}=${value}`;
        })
        .join("\n");
      return `${header}\n${vars}`;
    })
    .join("\n\n");
}
