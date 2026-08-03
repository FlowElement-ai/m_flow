/**
 * Troubleshooting Content Data
 *
 * Contains all troubleshooting content including common issues,
 * diagnostic checks, and solution guides for the Setup page.
 *
 * Content is organized into:
 * 1. Common Issues - Frequently encountered problems
 * 2. Diagnostic Checks - Automated health checks
 * 3. Solution Guides - Step-by-step fixes
 * 4. FAQ - Quick answers
 */

import type { CommonIssue, DiagnosticResult, ProbeKey } from "@/types/setup";

// ============================================================================
// Types
// ============================================================================

export interface DiagnosticCheck {
  id: string;
  name: string;
  description: string;
  category: "connectivity" | "configuration" | "performance" | "data";
  service?: ProbeKey;
  checkFn?: () => Promise<DiagnosticResult>;
}

export interface FAQ {
  id: string;
  question: string;
  answer: string;
  category: string;
  relatedIssues?: string[];
}

export interface SolutionStep {
  step: number;
  title: string;
  description: string;
  code?: string;
  codeLanguage?: "bash" | "python" | "env";
  warning?: string;
  tip?: string;
}

export interface SolutionGuide {
  id: string;
  title: string;
  description: string;
  difficulty: "easy" | "medium" | "hard";
  estimatedTime: string;
  steps: SolutionStep[];
  relatedServices: ProbeKey[];
}

// ============================================================================
// Common Issues
// ============================================================================

export const COMMON_ISSUES: CommonIssue[] = [
  {
    id: "backend-not-running",
    title: "Backend Server Not Running",
    problem: "The M-Flow backend server is not responding to requests",
    detection: "Health check returns connection refused or timeout",
    solution: [
      "Check if the server process is running: `ps aux | grep m_flow`",
      "Start the server: `uv run python -m m_flow.api.client`",
      "Check server logs for startup errors",
      "Verify the correct port (default: 8000) is not in use",
    ],
    relatedServices: ["relational_db", "vector_db", "graph_db"],
  },
  {
    id: "database-connection-failed",
    title: "Database Connection Failed",
    problem: "Unable to connect to the relational database (SQLite/PostgreSQL)",
    detection: "Relational DB probe shows 'down' status",
    solution: [
      "Check RELATIONAL_DB_PROVIDER in .env is set correctly",
      "For SQLite: Ensure the database file path is writable",
      "For PostgreSQL: Verify connection URL and credentials",
      "Check if PostgreSQL service is running",
      "Test connection manually: `psql -h localhost -U user -d mflow`",
    ],
    relatedServices: ["relational_db"],
  },
  {
    id: "vector-db-unavailable",
    title: "Vector Database Unavailable",
    problem: "Cannot connect to the vector database (LanceDB/Qdrant/etc.)",
    detection: "Vector DB probe shows 'down' status",
    solution: [
      "Check VECTOR_DB_PROVIDER in .env is set correctly",
      "For LanceDB: Ensure the data directory exists and is writable",
      "For Qdrant: Verify the server is running on the configured port",
      "Check API key if using a cloud provider",
      "Restart the vector database service",
    ],
    relatedServices: ["vector_db"],
  },
  {
    id: "graph-db-connection-error",
    title: "Graph Database Connection Error",
    problem: "Unable to connect to the knowledge graph database",
    detection: "Graph DB probe shows 'down' status",
    solution: [
      "Check GRAPH_DATABASE_PROVIDER in .env",
      "For Kuzu: Ensure the data directory exists",
      "For Neo4j: Verify bolt URL, username, and password",
      "Check if Neo4j service is running: `neo4j status`",
      "Test Neo4j connection in browser: http://localhost:7474",
    ],
    relatedServices: ["graph_db"],
  },
  {
    id: "llm-api-error",
    title: "LLM API Connection Error",
    problem: "Cannot connect to the LLM provider API",
    detection: "LLM Provider probe shows 'down' or 'warn' status",
    solution: [
      "Verify LLM_PROVIDER and LLM_MODEL in .env are correct",
      "Check API key is valid and has not expired",
      "Ensure you have sufficient API credits/quota",
      "For Ollama: Check if ollama server is running",
      "Test API manually with curl or provider's CLI tool",
    ],
    relatedServices: ["llm_provider"],
  },
  {
    id: "embedding-service-error",
    title: "Embedding Service Error",
    problem: "Embedding generation is failing",
    detection: "Embedding Service probe shows 'down' status",
    solution: [
      "Check EMBEDDING_PROVIDER and EMBEDDING_MODEL in .env",
      "Verify embedding API key if using external service",
      "For local models: Ensure model is downloaded",
      "Check embedding dimensions match vector DB configuration",
      "Restart the embedding service",
    ],
    relatedServices: ["embedding_service"],
  },
  {
    id: "storage-permission-error",
    title: "File Storage Permission Error",
    problem: "Cannot read or write files to storage",
    detection: "File Storage probe shows 'down' status",
    solution: [
      "Check FILE_STORAGE_PROVIDER in .env",
      "Verify FILE_STORAGE_PATH directory exists",
      "Check directory permissions: `ls -la /path/to/storage`",
      "For S3: Verify AWS credentials and bucket permissions",
      "Create directory if missing: `mkdir -p /path/to/storage`",
    ],
    relatedServices: ["file_storage"],
  },
  {
    id: "memory-ingestion-slow",
    title: "Memory Ingestion Very Slow",
    problem: "Document ingestion takes much longer than expected",
    detection: "Ingestion operations timeout or take minutes",
    solution: [
      "Check document size - large documents are slower",
      "Reduce batch size in ingestion settings",
      "Check LLM API response times",
      "Monitor system resources (CPU, memory, disk)",
      "Consider using async ingestion for large batches",
    ],
    relatedServices: ["llm_provider", "embedding_service", "vector_db"],
  },
  {
    id: "search-no-results",
    title: "Search Returns No Results",
    problem: "Episodic search queries return empty results",
    detection: "Search API returns empty array despite having data",
    solution: [
      "Verify documents have been successfully ingested",
      "Check if embeddings were generated correctly",
      "Try different search modes (EPISODIC, TRIPLET_COMPLETION)",
      "Increase top_k parameter",
      "Check if query is too specific or unrelated to content",
    ],
    relatedServices: ["vector_db", "embedding_service"],
  },
  {
    id: "env-file-not-loaded",
    title: "Environment Variables Not Loading",
    problem: ".env file changes are not taking effect",
    detection: "Settings show default values despite .env changes",
    solution: [
      "Restart the server after changing .env",
      "Verify .env file is in the project root directory",
      "Check .env file syntax (no spaces around =)",
      "Ensure no conflicting environment variables",
      "Use `printenv | grep M_FLOW` to check loaded vars",
    ],
    relatedServices: [],
  },
];

// ============================================================================
// Diagnostic Checks
// ============================================================================

export const DIAGNOSTIC_CHECKS: DiagnosticCheck[] = [
  {
    id: "server-reachable",
    name: "Server Reachability",
    description: "Check if the M-Flow server is reachable",
    category: "connectivity",
  },
  {
    id: "db-connections",
    name: "Database Connections",
    description: "Verify all database connections are working",
    category: "connectivity",
    service: "relational_db",
  },
  {
    id: "vector-store",
    name: "Vector Store Health",
    description: "Check vector database is operational",
    category: "connectivity",
    service: "vector_db",
  },
  {
    id: "graph-store",
    name: "Graph Store Health",
    description: "Check knowledge graph database is operational",
    category: "connectivity",
    service: "graph_db",
  },
  {
    id: "llm-api",
    name: "LLM API Access",
    description: "Verify LLM provider API is accessible",
    category: "connectivity",
    service: "llm_provider",
  },
  {
    id: "embedding-api",
    name: "Embedding API Access",
    description: "Verify embedding service is accessible",
    category: "connectivity",
    service: "embedding_service",
  },
  {
    id: "file-storage",
    name: "File Storage Access",
    description: "Check file storage read/write permissions",
    category: "connectivity",
    service: "file_storage",
  },
  {
    id: "env-config",
    name: "Environment Configuration",
    description: "Verify required environment variables are set",
    category: "configuration",
  },
  {
    id: "api-response-time",
    name: "API Response Time",
    description: "Measure API latency and performance",
    category: "performance",
  },
  {
    id: "memory-usage",
    name: "Memory Usage",
    description: "Check system memory utilization",
    category: "performance",
  },
  {
    id: "data-integrity",
    name: "Data Integrity",
    description: "Verify knowledge graph data consistency",
    category: "data",
  },
  {
    id: "index-status",
    name: "Index Status",
    description: "Check vector and graph indexes",
    category: "data",
  },
];

// ============================================================================
// Solution Guides
// ============================================================================

export const SOLUTION_GUIDES: SolutionGuide[] = [
  {
    id: "fresh-install",
    title: "Fresh Installation Setup",
    description: "Complete guide to setting up M-Flow from scratch",
    difficulty: "easy",
    estimatedTime: "10 minutes",
    steps: [
      {
        step: 1,
        title: "Clone the repository",
        description: "Get the latest M-Flow source code",
        code: "git clone https://github.com/FlowElement-xinliuyuansu/m_flow.git\ncd m_flow",
        codeLanguage: "bash",
      },
      {
        step: 2,
        title: "Install dependencies",
        description: "Install Python dependencies using uv",
        code: "uv sync --dev --all-extras",
        codeLanguage: "bash",
        tip: "Make sure you have uv installed: curl -LsSf https://astral.sh/uv/install.sh | sh",
      },
      {
        step: 3,
        title: "Create environment file",
        description: "Copy the template and configure your settings",
        code: "cp .env.template .env\n# Edit .env with your configuration",
        codeLanguage: "bash",
      },
      {
        step: 4,
        title: "Configure LLM provider",
        description: "Set your LLM provider and API key",
        code: "LLM_PROVIDER=openai\nLLM_MODEL=gpt-4o\nOPENAI_API_KEY=sk-...",
        codeLanguage: "env",
        warning: "Never commit your API keys to version control",
      },
      {
        step: 5,
        title: "Start the server",
        description: "Launch the M-Flow backend",
        code: "uv run python -m m_flow.api.client",
        codeLanguage: "bash",
      },
      {
        step: 6,
        title: "Verify installation",
        description: "Check that all services are running",
        code: "curl http://localhost:8000/health/detailed",
        codeLanguage: "bash",
      },
    ],
    relatedServices: ["relational_db", "vector_db", "graph_db", "llm_provider"],
  },
  {
    id: "reset-databases",
    title: "Reset All Databases",
    description: "Clear all data and start fresh (destructive)",
    difficulty: "medium",
    estimatedTime: "5 minutes",
    steps: [
      {
        step: 1,
        title: "Stop the server",
        description: "Ensure M-Flow is not running",
        code: "# Find and kill the process\npkill -f 'm_flow.api.client'",
        codeLanguage: "bash",
        warning: "This will delete ALL your data!",
      },
      {
        step: 2,
        title: "Backup existing data (optional)",
        description: "Save your data before resetting",
        code: "cp -r data/ data_backup_$(date +%Y%m%d)/",
        codeLanguage: "bash",
        tip: "Always backup before destructive operations",
      },
      {
        step: 3,
        title: "Remove database files",
        description: "Delete SQLite and embedded DB files",
        code: "rm -rf data/*.db\nrm -rf data/lancedb/\nrm -rf data/kuzu/",
        codeLanguage: "bash",
      },
      {
        step: 4,
        title: "Restart the server",
        description: "Server will recreate databases on startup",
        code: "uv run python -m m_flow.api.client",
        codeLanguage: "bash",
      },
    ],
    relatedServices: ["relational_db", "vector_db", "graph_db"],
  },
  {
    id: "switch-llm-provider",
    title: "Switch LLM Provider",
    description: "Change from one LLM provider to another",
    difficulty: "easy",
    estimatedTime: "5 minutes",
    steps: [
      {
        step: 1,
        title: "Update .env configuration",
        description: "Change the LLM provider settings",
        code: "# For OpenAI\nLLM_PROVIDER=openai\nLLM_MODEL=gpt-4o\nOPENAI_API_KEY=sk-...\n\n# For Anthropic\nLLM_PROVIDER=anthropic\nLLM_MODEL=claude-3-5-sonnet-20241022\nANTHROPIC_API_KEY=sk-ant-...\n\n# For Ollama (local)\nLLM_PROVIDER=ollama\nLLM_MODEL=llama3.2\nOLLAMA_BASE_URL=http://localhost:11434",
        codeLanguage: "env",
      },
      {
        step: 2,
        title: "Restart the server",
        description: "Apply the new configuration",
        code: "# Stop and restart the server\npkill -f 'm_flow.api.client'\nuv run python -m m_flow.api.client",
        codeLanguage: "bash",
      },
      {
        step: 3,
        title: "Verify the change",
        description: "Check that the new provider is active",
        code: "curl http://localhost:8000/health/detailed | jq '.probes.llm_provider'",
        codeLanguage: "bash",
      },
    ],
    relatedServices: ["llm_provider"],
  },
  {
    id: "configure-neo4j",
    title: "Configure Neo4j Graph Database",
    description: "Set up Neo4j for production use",
    difficulty: "medium",
    estimatedTime: "15 minutes",
    steps: [
      {
        step: 1,
        title: "Install Neo4j",
        description: "Install Neo4j Community or Enterprise edition",
        code: "# Using Docker\ndocker run -d \\\n  --name neo4j \\\n  -p 7474:7474 -p 7687:7687 \\\n  -e NEO4J_AUTH=neo4j/password \\\n  neo4j:latest",
        codeLanguage: "bash",
        tip: "You can also install Neo4j Desktop for easier management",
      },
      {
        step: 2,
        title: "Verify Neo4j is running",
        description: "Check the Neo4j browser",
        code: "# Open in browser: http://localhost:7474\n# Login with neo4j/password",
        codeLanguage: "bash",
      },
      {
        step: 3,
        title: "Configure M-Flow",
        description: "Update .env with Neo4j connection details",
        code: "GRAPH_DATABASE_PROVIDER=neo4j\nGRAPH_DATABASE_URL=bolt://localhost:7687\nGRAPH_DATABASE_USERNAME=neo4j\nGRAPH_DATABASE_PASSWORD=password",
        codeLanguage: "env",
        warning: "Use a strong password in production",
      },
      {
        step: 4,
        title: "Restart M-Flow",
        description: "Apply the configuration",
        code: "pkill -f 'm_flow.api.client'\nuv run python -m m_flow.api.client",
        codeLanguage: "bash",
      },
    ],
    relatedServices: ["graph_db"],
  },
  {
    id: "debug-ingestion",
    title: "Debug Ingestion Issues",
    description: "Troubleshoot document ingestion problems",
    difficulty: "medium",
    estimatedTime: "10 minutes",
    steps: [
      {
        step: 1,
        title: "Enable debug logging",
        description: "Increase log verbosity",
        code: "export LOG_LEVEL=DEBUG\nuv run python -m m_flow.api.client",
        codeLanguage: "bash",
      },
      {
        step: 2,
        title: "Test with a simple document",
        description: "Try ingesting a small text file",
        code: "import m_flow\n\nresult = await m_flow.add(\n    data=\"This is a test document.\",\n    dataset_name=\"debug_test\"\n)\nprint(result)",
        codeLanguage: "python",
      },
      {
        step: 3,
        title: "Check LLM responses",
        description: "Verify the LLM is extracting entities correctly",
        code: "# Look for extraction results in logs\ngrep -i 'extract' server.log | tail -20",
        codeLanguage: "bash",
      },
      {
        step: 4,
        title: "Verify embeddings",
        description: "Check if embeddings are being generated",
        code: "# Query the vector database directly\ncurl http://localhost:8000/api/v1/search \\\n  -H 'Content-Type: application/json' \\\n  -d '{\"query_text\": \"test\", \"top_k\": 5}'",
        codeLanguage: "bash",
      },
    ],
    relatedServices: ["llm_provider", "embedding_service", "vector_db"],
  },
];

// ============================================================================
// FAQ
// ============================================================================

export const FAQS: FAQ[] = [
  {
    id: "supported-llms",
    question: "Which LLM providers are supported?",
    answer: "M-Flow supports OpenAI, Anthropic, Google Gemini, Mistral AI, AWS Bedrock, and local models via Ollama. Configure your preferred provider in the .env file using LLM_PROVIDER and LLM_MODEL.",
    category: "Configuration",
  },
  {
    id: "default-databases",
    question: "What databases does M-Flow use by default?",
    answer: "By default, M-Flow uses SQLite for relational data, LanceDB for vector storage, and Kuzu for the knowledge graph. These are embedded databases that require no additional setup.",
    category: "Configuration",
  },
  {
    id: "production-databases",
    question: "Which databases should I use in production?",
    answer: "For production, consider PostgreSQL for relational data, Qdrant or Milvus for vectors, and Neo4j for the knowledge graph. These provide better scalability and reliability.",
    category: "Configuration",
  },
  {
    id: "api-key-security",
    question: "How do I secure my API keys?",
    answer: "Store API keys in the .env file which should never be committed to version control. Add .env to your .gitignore file. For production, use environment variables or a secrets manager.",
    category: "Security",
  },
  {
    id: "memory-types",
    question: "What are the different memory types?",
    answer: "M-Flow supports Episodic memory (events and experiences), Semantic memory (facts and concepts), and Procedural memory (skills and processes). These are stored as Episodes, Facets, FacetPoints, and Concepts in the knowledge graph.",
    category: "Concepts",
  },
  {
    id: "retrieval-modes",
    question: "What retrieval modes are available?",
    answer: "Available modes: TRIPLET_COMPLETION (complete partial knowledge), EPISODIC (event-based), PROCEDURAL (skill-based), CYPHER (direct graph query), and CHUNKS_LEXICAL (keyword search).",
    category: "Concepts",
  },
  {
    id: "restart-required",
    question: "When do I need to restart the server?",
    answer: "Restart is required after changing .env configuration (except LLM settings which can be updated via API). Database, embedding, and storage configurations require a restart.",
    category: "Operations",
  },
  {
    id: "data-persistence",
    question: "Where is my data stored?",
    answer: "By default, data is stored in the ./data directory. SQLite database, LanceDB vectors, and Kuzu graph are all stored there. Configure FILE_STORAGE_PATH to change the location.",
    category: "Operations",
  },
];

// ============================================================================
// Issue Categories
// ============================================================================

export const ISSUE_CATEGORIES = {
  connectivity: {
    title: "Connectivity Issues",
    description: "Problems connecting to services",
    icon: "Wifi",
  },
  configuration: {
    title: "Configuration Issues",
    description: "Settings and environment problems",
    icon: "Settings",
  },
  performance: {
    title: "Performance Issues",
    description: "Slow or unresponsive operations",
    icon: "Gauge",
  },
  data: {
    title: "Data Issues",
    description: "Data integrity and retrieval problems",
    icon: "Database",
  },
} as const;

// ============================================================================
// Helper Functions
// ============================================================================

/**
 * Get issues by related service
 */
export function getIssuesByService(service: ProbeKey): CommonIssue[] {
  return COMMON_ISSUES.filter((issue) =>
    issue.relatedServices.includes(service)
  );
}

/**
 * Get diagnostics by category
 */
export function getDiagnosticsByCategory(
  category: DiagnosticCheck["category"]
): DiagnosticCheck[] {
  return DIAGNOSTIC_CHECKS.filter((check) => check.category === category);
}

/**
 * Get FAQs by category
 */
export function getFAQsByCategory(category: string): FAQ[] {
  return FAQS.filter((faq) => faq.category === category);
}

/**
 * Search issues by keyword
 */
export function searchIssues(query: string): CommonIssue[] {
  const lowerQuery = query.toLowerCase();
  return COMMON_ISSUES.filter(
    (issue) =>
      issue.title.toLowerCase().includes(lowerQuery) ||
      issue.problem.toLowerCase().includes(lowerQuery) ||
      issue.solution.some((s) => s.toLowerCase().includes(lowerQuery))
  );
}

/**
 * Get solution guide by ID
 */
export function getSolutionGuide(id: string): SolutionGuide | undefined {
  return SOLUTION_GUIDES.find((guide) => guide.id === id);
}
