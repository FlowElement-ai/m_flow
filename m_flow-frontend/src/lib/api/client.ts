import {
  SearchOptions,
  SearchResponse,
  SearchResultItem,
  SearchHistoryEntry,
  ActivityItem,
  ActivePipeline,
  Dataset,
  DatasetWithCounts,
  DataItemInfo,
  GraphData,
  AuthUser,
  UserListItem,
  LoginRequest,
  RegisterRequest,
  MemorizeRequest,
  MemorizeResponse,
  HealthCheckResponse,
  DetailedHealthResponse,
  Settings,
  ApiError,
  ManualIngestRequest,
  ManualIngestResult,
  IngestTextRequest,
  IngestUploadOptions,
  IngestResponse,
  Prompt,
  PromptCategory,
  EpisodesOverviewResponse,
  EntityNetworkResponse,
  EpisodeQualityResponse,
  SizeCheckResponse,
  CorefConfig,
  CorefConfigUpdate,
  CorefStats,
  DeleteDocumentParams,
  DeleteDocumentResponse,
  SyncRequest,
  SyncResponse,
  SyncStatusResponse,
  DatasetStatusResponse,
  PatchNodeRequest,
  PatchNodeResult,
  PruneResponse,
  PruneSystemRequest,
  ResponsesRequest,
  ResponsesResponse,
  SearchOptionsWithCoref,
} from "@/types";
import { config, STORAGE_KEYS } from "@/lib/config";

// ============================================================================
// Node Deletion Types
// ============================================================================

export interface DeletionPreview {
  node_id: string;
  node_type: string;
  node_name: string;
  edge_count: number;
  neighbor_types: string[];
  vector_collections: string[];
  warning: string;
  can_delete: boolean;
}

export interface DeletionResult {
  status: string;
  node_id: string;
  node_type: string;
  cascade: boolean;
  deleted_count: number;
  deleted_ids: string[];
}

// ============================================================================
// Error Handling
// ============================================================================

export class ServiceFault extends Error {
  constructor(
    public code: string,
    message: string,
    public details?: Record<string, unknown>
  ) {
    super(message);
    this.name = "ServiceFault";
  }

  /**
   * Create error object from backend response
   * 
   * Compatible with multiple backend error formats:
   *   - { code, message } - Standard format
   *   - { error: "..." } - Simplified format
   *   - { detail: "..." } - FastAPI default format
   *   - { detail: [...] } - FastAPI validation error format
   */
  static fromResponse(data: ApiError | { error?: string; detail?: string | unknown[] }): ServiceFault {
    // Prefer standard format
    if ("code" in data && "message" in data) {
      return new ServiceFault(
        data.code || "UNKNOWN_ERROR",
        data.message || "Unknown error",
        (data as ApiError).details
      );
    }

    // Handle { error: "..." } format
    if ("error" in data && typeof data.error === "string") {
      return new ServiceFault("API_ERROR", data.error);
    }

    // Handle FastAPI default { detail: "..." } format
    if ("detail" in data) {
      if (typeof data.detail === "string") {
        return new ServiceFault("API_ERROR", data.detail);
      }
      // Handle validation error array { detail: [{loc, msg, type}, ...] }
      if (Array.isArray(data.detail)) {
        const errors = data.detail as Array<{ msg?: string; loc?: string[] }>;
        const messages = errors.map((err) => {
          const loc = err.loc?.join(".") || "unknown";
          return `${loc}: ${err.msg || "validation error"}`;
        });
        return new ServiceFault("VALIDATION_ERROR", messages.join("; "), { detail: data.detail });
      }
    }

    // Fallback: unable to parse format
    return new ServiceFault("UNKNOWN_ERROR", JSON.stringify(data));
  }
}

// ============================================================================
// M-Flow API Client
// ============================================================================

export class MflowApiClient {
  private baseUrl: string;
  private token: string | null = null;

  constructor(baseUrl: string = config.API_BASE_URL) {
    this.baseUrl = baseUrl;
    // Restore token from localStorage
    if (typeof window !== "undefined") {
      const savedToken = localStorage.getItem(STORAGE_KEYS.AUTH_TOKEN);
      if (savedToken) {
        this.token = savedToken;
      }
    }
  }

  // --------------------------------------------------------------------------
  // Internal Methods
  // --------------------------------------------------------------------------

  private async request<T>(
    endpoint: string,
    options: RequestInit = {}
  ): Promise<T> {
    const url = `${this.baseUrl}${endpoint}`;
    const headers: HeadersInit = {
      "Content-Type": "application/json",
      ...options.headers,
    };

    if (this.token) {
      (headers as Record<string, string>)["Authorization"] = `Bearer ${this.token}`;
    }

    const response = await fetch(url, {
      ...options,
      headers,
      credentials: "include",
    });

    if (!response.ok) {
      // 401 Unauthorized: Token expired or invalid
      // Important: Don't call this.logout() as it would cause circular requests (logout also calls API)
      if (response.status === 401) {
        this.token = null;
        if (typeof window !== "undefined") {
          localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
          // Dynamic import to avoid circular dependency, clear Zustand auth store
          import("@/lib/store").then(({ useAuthStore }) => {
            useAuthStore.getState().logout();
          });
          // Redirect to home page (will auto-display login UI)
          // Use setTimeout to ensure store cleanup completes
          setTimeout(() => {
            window.location.href = "/";
          }, 100);
        }
        throw new ServiceFault("UNAUTHORIZED", "Session expired, please login again");
      }

      const errorData = await response.json().catch(() => ({
        code: "UNKNOWN_ERROR",
        message: `HTTP ${response.status}: ${response.statusText}`,
      }));
      throw ServiceFault.fromResponse(errorData);
    }

    // Handle empty response
    const text = await response.text();
    if (!text) return {} as T;
    return JSON.parse(text);
  }

  setToken(token: string | null): void {
    this.token = token;
  }

  // --------------------------------------------------------------------------
  // Health Check
  // --------------------------------------------------------------------------

  /**
   * Simple health check
   * 
   * @endpoint GET /health
   * @description Basic liveness/readiness probe for container orchestration
   * @returns HealthCheckResponse with status and version
   * 
   * @example
   * const health = await apiClient.healthCheck();
   * if (health.status === 'healthy') {
   *   console.log('Backend is running');
   * }
   */
  async healthCheck(): Promise<HealthCheckResponse> {
    const raw = await this.request<{ status: string; health: string; version: string }>("/health");
    const isUp = raw.health === "up" || raw.health === "warn" || raw.status === "ready";
    return {
      status: isUp ? "healthy" : "unhealthy",
      version: raw.version,
    };
  }

  /**
   * Detailed health check
   * 
   * @endpoint GET /health/detailed
   * @description Get detailed health status of all system components, including latency and error info
   * @returns DetailedHealthResponse with all component probe results
   * 
   * @example
   * const detailed = await apiClient.healthCheckDetailed();
   * 
   * // Check overall status
   * if (detailed.verdict === 'up') {
   *   console.log('All systems operational');
   * }
   * 
   * // Check specific component
   * if (detailed.probes.llm_provider.verdict !== 'up') {
   *   console.warn('LLM is not responding:', detailed.probes.llm_provider.note);
   * }
   * 
   * // Get latency info
   * console.log(`Database latency: ${detailed.probes.relational_db.latency_ms}ms`);
   * 
   * @throws ServiceFault when service is completely unavailable
   */
  async healthCheckDetailed(): Promise<DetailedHealthResponse> {
    return this.request<DetailedHealthResponse>("/health/detailed");
  }

  // --------------------------------------------------------------------------
  // Authentication
  // --------------------------------------------------------------------------

  async login(credentials: LoginRequest): Promise<AuthUser> {
    const formData = new URLSearchParams();
    formData.append("username", credentials.username);
    formData.append("password", credentials.password);

    const response = await fetch(`${this.baseUrl}/api/v1/auth/login`, {
      method: "POST",
      headers: { "Content-Type": "application/x-www-form-urlencoded" },
      body: formData,
      credentials: "include",
    });

    if (!response.ok) {
      throw new ServiceFault("AUTH_FAILED", "Login failed, please check username and password");
    }

    // Save token
    const data = await response.json();
    if (data.access_token) {
      this.setToken(data.access_token);
      // Save to localStorage
      if (typeof window !== "undefined") {
        localStorage.setItem(STORAGE_KEYS.AUTH_TOKEN, data.access_token);
      }
    }

    return this.getCurrentUser();
  }

  async register(data: RegisterRequest): Promise<AuthUser> {
    return this.request<AuthUser>("/api/v1/auth/register", {
      method: "POST",
      body: JSON.stringify(data),
    });
  }

  async logout(): Promise<void> {
    try {
      await this.request("/api/v1/auth/logout", { method: "POST" });
    } finally {
      // Regardless of API call success, clear local state
      // Ensure proper logout even if backend is unavailable or token is invalid
      this.token = null;
      if (typeof window !== "undefined") {
        localStorage.removeItem(STORAGE_KEYS.AUTH_TOKEN);
      }
    }
  }

  async getCurrentUser(): Promise<AuthUser> {
    return this.request<AuthUser>("/api/v1/users/me");
  }

  // --------------------------------------------------------------------------
  // User Management
  // --------------------------------------------------------------------------

  /**
   * Get all users (superuser only)
   *
   * @endpoint GET /api/v1/users
   */
  async getUsers(): Promise<UserListItem[]> {
    return this.request<UserListItem[]>("/api/v1/users");
  }

  /**
   * Get user by ID
   *
   * @endpoint GET /api/v1/users/{id}
   */
  async getUser(id: string): Promise<UserListItem> {
    return this.request<UserListItem>(`/api/v1/users/${id}`);
  }

  /**
   * Create user via public registration endpoint
   *
   * @endpoint POST /api/v1/auth/register
   * @note This is the PUBLIC registration endpoint - NO auth required!
   *       Any user can call this to create a new account.
   *       Frontend should hide this button for non-superusers if admin-only creation is desired.
   */
  async createUser(data: RegisterRequest): Promise<AuthUser> {
    return this.register(data);
  }

  /**
   * Delete user (superuser only)
   *
   * @endpoint DELETE /api/v1/users/{id}
   */
  async deleteUser(id: string): Promise<void> {
    await this.request(`/api/v1/users/${id}`, { method: "DELETE" });
  }

  // --------------------------------------------------------------------------
  // Dataset Management
  // --------------------------------------------------------------------------

  async getDatasets(): Promise<Dataset[]> {
    return this.request<Dataset[]>("/api/v1/datasets");
  }

  async getDatasetsWithCounts(): Promise<DatasetWithCounts[]> {
    return this.request<DatasetWithCounts[]>("/api/v1/datasets?with_counts=true");
  }

  async getDataset(id: string): Promise<DatasetWithCounts> {
    return this.request<DatasetWithCounts>(`/api/v1/datasets/${id}`);
  }

  async deleteDataset(id: string): Promise<void> {
    await this.request(`/api/v1/datasets/${id}`, { method: "DELETE" });
  }

  /**
   * Create a new dataset
   * 
   * @endpoint POST /api/v1/datasets
   * @param name Dataset name (1-255 characters)
   * @returns Created dataset (or existing dataset if name already exists - idempotent)
   */
  async createDataset(name: string): Promise<Dataset> {
    return this.request<Dataset>("/api/v1/datasets", {
      method: "POST",
      body: JSON.stringify({ name }),
    });
  }

  // --------------------------------------------------------------------------
  // Data Upload & Ingestion
  // --------------------------------------------------------------------------

  /**
   * Add text content to dataset
   * 
   * @endpoint POST /api/v1/add
   * @param text Text content
   * @param datasetName Dataset name (mutually exclusive with datasetId)
   * @param datasetId Dataset UUID (mutually exclusive with datasetName)
   * @param nodeSet Node identifiers
   */
  async addTextMemory(
    text: string, 
    datasetName?: string, 
    datasetId?: string,
    nodeSet?: string[],
    incrementalLoading?: boolean
  ): Promise<void> {
    const blob = new Blob([text], { type: "text/plain" });
    const file = new File([blob], `text_${crypto.randomUUID()}.txt`, { type: "text/plain" });
    await this.uploadFile(file, datasetName || "main_dataset", datasetId, nodeSet, incrementalLoading);
  }

  /**
   * Upload file to dataset
   * 
   * @endpoint POST /api/v1/add
   * @param file File to upload
   * @param datasetName Dataset name (mutually exclusive with datasetId)
   * @param datasetId Dataset UUID (mutually exclusive with datasetName)
   * @param nodeSet Node identifiers
   * @param incrementalLoading Skip already processed files
   */
  async uploadFile(
    file: File, 
    datasetName?: string, 
    datasetId?: string,
    nodeSet?: string[],
    incrementalLoading?: boolean
  ): Promise<void> {
    const formData = new FormData();
    formData.append("data", file);
    
    // Prefer datasetId, otherwise use datasetName
    if (datasetId) {
      formData.append("datasetId", datasetId);
    } else if (datasetName) {
      formData.append("datasetName", datasetName);
    }
    
    if (nodeSet && nodeSet.length > 0) {
      nodeSet.forEach((node) => formData.append("graph_scope", node));
    }
    
    if (incrementalLoading !== undefined) {
      formData.append("incremental_loading", String(incrementalLoading));
    }

    const headers: HeadersInit = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const response = await fetch(`${this.baseUrl}/api/v1/add`, {
      method: "POST",
      body: formData,
      headers,
      credentials: "include",
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({
        code: "UPLOAD_FAILED",
        message: "File upload failed",
      }));
      throw ServiceFault.fromResponse(errorData);
    }
  }

  /**
   * Execute Memorize operation - build knowledge graph
   * 
   * @endpoint POST /api/v1/memorize
   * @returns dict[dataset_id -> RunEvent]
   * @description Returns pipeline run info for each dataset, containing workflow_run_id for WebSocket progress tracking
   */
  async memorize(request: MemorizeRequest = {}): Promise<MemorizeResponse> {
    return this.request<MemorizeResponse>("/api/v1/memorize", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  /**
   * Manual ingestion - directly add structured memories to knowledge graph
   * 
   * @endpoint POST /api/v1/manual/ingest
   * @description Bypass LLM extraction process, directly specify Episode/Facet/Entity structure
   */
  async manualIngest(request: ManualIngestRequest): Promise<ManualIngestResult> {
    return this.request<ManualIngestResult>("/api/v1/manual/ingest", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  // --------------------------------------------------------------------------
  // Unified Ingestion (Ingest = Add + Memorize)
  // --------------------------------------------------------------------------

  /**
   * Unified ingestion - text content
   * 
   * @endpoint POST /api/v1/ingest
   * @description Complete data ingestion and knowledge graph construction in one step
   * 
   * @example
   * // Simple usage
   * const result = await apiClient.ingestText({
   *   content: "Document content...",
   *   dataset_name: "my_docs",
   * });
   * 
   * // Background processing
   * const result = await apiClient.ingestText({
   *   content: "Long document...",
   *   run_in_background: true,
   * });
   * 
   * // With feature toggles
   * const result = await apiClient.ingestText({
   *   content: "...",
   *   enable_episode_routing: false,  // Disable episode routing
   *   enable_procedural: true,        // Enable procedural memory
   * });
   */
  async ingestText(request: IngestTextRequest): Promise<IngestResponse> {
    return this.request<IngestResponse>("/api/v1/ingest", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  /**
   * Unified ingestion - file upload
   * 
   * @endpoint POST /api/v1/ingest/upload
   * @description Upload file and complete ingestion and knowledge graph construction in one step
   * 
   * @example
   * // Single file upload
   * const result = await apiClient.ingestFile(file, {
   *   datasetName: "my_docs",
   * });
   * 
   * // Multiple file upload
   * const result = await apiClient.ingestFiles(fileList, {
   *   datasetName: "my_docs",
   *   runInBackground: true,
   * });
   * 
   * // With feature toggles
   * const result = await apiClient.ingestFile(file, {
   *   enableEpisodeRouting: false,
   *   enableProcedural: true,
   * });
   */
  async ingestFile(file: File, options: IngestUploadOptions = {}): Promise<IngestResponse> {
    return this.ingestFiles([file], options);
  }

  /**
   * Unified ingestion - multiple file upload
   * 
   * @endpoint POST /api/v1/ingest/upload
   */
  async ingestFiles(files: File[], options: IngestUploadOptions = {}): Promise<IngestResponse> {
    const formData = new FormData();
    
    // Add files
    files.forEach((file) => formData.append("data", file));
    
    // Add optional parameters
    if (options.datasetName) {
      formData.append("datasetName", options.datasetName);
    }
    if (options.datasetId) {
      formData.append("datasetId", options.datasetId);
    }
    if (options.nodeSet && options.nodeSet.length > 0) {
      options.nodeSet.forEach((node) => formData.append("graph_scope", node));
    }
    if (options.skipMemorize !== undefined) {
      formData.append("skip_memorize", String(options.skipMemorize));
    }
    if (options.runInBackground !== undefined) {
      formData.append("run_in_background", String(options.runInBackground));
    }
    if (options.chunkSize !== undefined) {
      formData.append("chunk_size", String(options.chunkSize));
    }
    if (options.chunksPerBatch !== undefined) {
      formData.append("chunks_per_batch", String(options.chunksPerBatch));
    }
    
    // Feature toggles
    if (options.enableEpisodeRouting !== undefined) {
      formData.append("enable_episode_routing", String(options.enableEpisodeRouting));
    }
    if (options.enableContentRouting !== undefined) {
      formData.append("enable_content_routing", String(options.enableContentRouting));
    }
    if (options.enableProcedural !== undefined) {
      formData.append("enable_procedural", String(options.enableProcedural));
    }
    if (options.enableFacetPoints !== undefined) {
      formData.append("enable_facet_points", String(options.enableFacetPoints));
    }
    if (options.preciseMode !== undefined) {
      formData.append("precise_mode", String(options.preciseMode));
    }
    if (options.conflictMode) {
      formData.append("conflict_mode", options.conflictMode);
    }

    const headers: HeadersInit = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }

    const response = await fetch(`${this.baseUrl}/api/v1/ingest/upload`, {
      method: "POST",
      body: formData,
      headers,
      credentials: "include",
    });

    if (!response.ok) {
      const errorData = await response.json().catch(() => ({
        code: "INGEST_FAILED",
        message: "Ingestion failed",
      }));
      throw ServiceFault.fromResponse(errorData);
    }

    return response.json();
  }

  // --------------------------------------------------------------------------
  // Search
  // --------------------------------------------------------------------------

  /**
   * Get user search history
   * 
   * @endpoint GET /api/v1/search
   * @description Get current user's historical search records in reverse chronological order
   */
  async getSearchHistory(): Promise<SearchHistoryEntry[]> {
    return this.request<SearchHistoryEntry[]>("/api/v1/search");
  }

  /**
   * Get recent activities for dashboard
   * 
   * @endpoint GET /api/v1/activity
   * @description Aggregated activity feed from multiple sources (search queries, ingestions)
   */
  async getActivities(limit: number = 20): Promise<ActivityItem[]> {
    return this.request<ActivityItem[]>(`/api/v1/activity?limit=${limit}`);
  }

  /**
   * Get active pipeline runs
   * 
   * @endpoint GET /api/v1/pipeline/active
   * @description Get currently running pipeline operations with real-time progress
   */
  async getActivePipelines(): Promise<ActivePipeline[]> {
    const raw = await this.request<ActivePipeline[]>("/api/v1/pipeline/active");
    return raw.map(p => ({
      ...p,
      pipelineRunId: p.workflow_run_id ?? (p as any).workflowRunId ?? p.pipelineRunId,
      datasetId: p.dataset_id ?? p.datasetId ?? null,
      datasetName: p.dataset_name ?? p.datasetName ?? null,
      pipelineName: p.workflow_name ?? p.pipelineName ?? "Pipeline",
      totalItems: p.total_items ?? p.totalItems ?? null,
      processedItems: p.processed_items ?? p.processedItems ?? null,
      currentStep: p.current_step ?? p.currentStep ?? null,
      startedAt: p.started_at ?? p.startedAt ?? null,
      updatedAt: p.updated_at ?? p.updatedAt ?? null,
      createdAt: p.created_at ?? p.createdAt ?? null,
    }));
  }

  /**
   * Dismiss a stale pipeline
   * 
   * @endpoint DELETE /api/v1/pipeline/active/{pipelineRunId}
   * @description Mark a stale pipeline as errored so it no longer shows as active
   */
  async dismissPipeline(pipelineRunId: string): Promise<{ success: boolean; message: string }> {
    return this.request<{ success: boolean; message: string }>(
      `/api/v1/pipeline/active/${pipelineRunId}`,
      { method: "DELETE" }
    );
  }

  /**
   * Execute knowledge graph search
   *
   * @endpoint POST /api/v1/search
   * @description Search API supporting multiple recall modes (episodic, triplet, procedural, etc.)
   *
   * @note Backend return types:
   *   - Standard mode: Array<SearchResult>
   *   - Combined mode (use_combined_context=true): CombinedSearchResult
   *   - Permission denied: []
   */
  async search(options: SearchOptions): Promise<SearchResponse> {
    const payload = {
      recall_mode: options.recall_mode,
      query: options.query,
      datasets: options.datasets,
      dataset_ids: options.dataset_ids,
      system_prompt: options.system_prompt || undefined,
      node_name: options.node_name,
      top_k: options.top_k ?? 10,
      only_context: options.only_context ?? false,
      use_combined_context: options.use_combined_context ?? false,
      // Advanced search parameters
      wide_search_top_k: options.wide_search_top_k,
      triplet_distance_penalty: options.triplet_distance_penalty,
      verbose: options.verbose ?? false,
      // Episodic retrieval parameters (Phase 0.4)
      enable_hybrid_search: options.enable_hybrid_search,
      enable_time_bonus: options.enable_time_bonus,
      edge_miss_cost: options.edge_miss_cost,
      hop_cost: options.hop_cost,
      full_number_match_bonus: options.full_number_match_bonus,
      enable_adaptive_weights: options.enable_adaptive_weights,
      // Episodic output control
      display_mode: options.display_mode,
      max_facets_per_episode: options.max_facets_per_episode,
      max_points_per_facet: options.max_points_per_facet,
      collections: options.collections,
      keyword_match_bonus: options.keyword_match_bonus,
      direct_episode_penalty: options.direct_episode_penalty,
      coref_enabled: options.coref_enabled,
      coref_session_id: options.coref_session_id,
      coref_new_turn: options.coref_new_turn,
    };

    // Backend return type can be array or CombinedSearchResult object
    const rawResponse = await this.request<unknown>("/api/v1/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    // Handle CombinedSearchResult format (when use_combined_context=true)
    if (!Array.isArray(rawResponse)) {
      const combined = rawResponse as {
        result: unknown;
        context: Record<string, unknown>;
        graphs: unknown;
        datasets: unknown[];
      };

      // Extract display content from result or context
      let resultContent: string;
      if (combined.result == null) {
        // When result is null, extract info from context
        const contextValues = Object.values(combined.context || {}).map((v) =>
          typeof v === "string" ? v : JSON.stringify(v, null, 2)
        );
        resultContent = contextValues.join("\n\n") || "No results found";
      } else if (typeof combined.result === "string") {
        resultContent = combined.result;
      } else {
        resultContent = JSON.stringify(combined.result, null, 2);
      }

      return {
        results: [
          {
            id: "combined-0",
            content: resultContent,
            score: 1.0,
            source_dataset: "combined",
            metadata: combined.context,
          },
        ],
        total: 1,
        query: options.query,
        recall_mode: options.recall_mode,
      };
    }

    // Handle standard array format
    const results: SearchResultItem[] = [];
    for (const dsResult of rawResponse as Array<{
      search_result: unknown;
      dataset_id: string;
      dataset_name: string;
    }>) {
      const searchResult = dsResult.search_result;

      // Type-safe handling: backend search_result varies by mode:
      //   EPISODIC/TRIPLET only_context: [{"dataset_name": "context text"}]
      //   EPISODIC/TRIPLET normal:       ["LLM answer string"]
      //   CHUNKS_LEXICAL:                [[cf_dict, cf_dict, ...]]
      //   PROCEDURAL:                    null or [string]
      const flatItems: unknown[] = [];
      if (searchResult == null) {
        // skip
      } else if (Array.isArray(searchResult)) {
        for (const item of searchResult) {
          if (Array.isArray(item)) {
            for (const sub of item) {
              flatItems.push(sub);
            }
          } else if (typeof item === "object" && item !== null && !Array.isArray(item)) {
            // Dict like {"main_dataset": "context text"} from only_context mode
            const obj = item as Record<string, unknown>;
            const keys = Object.keys(obj);
            // If all values are strings, this is a dataset→context map; extract the text values
            const allStrings = keys.length > 0 && keys.every((k) => typeof obj[k] === "string");
            if (allStrings) {
              for (const k of keys) {
                flatItems.push(obj[k] as string);
              }
            } else {
              flatItems.push(item);
            }
          } else {
            flatItems.push(item);
          }
        }
      } else if (typeof searchResult === "string") {
        flatItems.push(searchResult);
      } else {
        flatItems.push(searchResult);
      }

      // Split concatenated results into individual items.
      // Summary mode uses "---" separator; detail/triplet context is kept as single block
      // (structured rendering is handled by UI components).
      for (const item of flatItems) {
        const str = typeof item === "string" ? item : JSON.stringify(item, null, 2);

        let blocks: string[];
        if (str.includes("\n\n---\n\n")) {
          blocks = str.split("\n\n---\n\n").map((b) => b.trim()).filter(Boolean);
        } else if (str.includes("\n---\n")) {
          blocks = str.split("\n---\n").map((b) => b.trim()).filter(Boolean);
        } else {
          blocks = [str];
        }

        for (const block of blocks) {
          results.push({
            id: `${dsResult.dataset_id}-${results.length}`,
            content: block,
            score: 1.0,
            source_dataset: dsResult.dataset_name,
          });
        }
      }
    }

    return {
      results,
      total: results.length,
      query: options.query,
      recall_mode: options.recall_mode,
    };
  }

  // --------------------------------------------------------------------------
  // Knowledge Graph
  // --------------------------------------------------------------------------

  async getGraph(datasetId?: string): Promise<GraphData> {
    const params = datasetId ? `?dataset_id=${datasetId}` : "";
    return this.request<GraphData>(`/api/v1/graph${params}`);
  }

  /**
   * Get episode subgraph with connected Facets and Entities.
   * @param datasetId - Optional dataset ID for multi-tenant support
   */
  async getEpisodeSubgraph(episodeId: string, datasetId?: string): Promise<GraphData> {
    const params = datasetId ? `?dataset_id=${datasetId}` : "";
    return this.request<GraphData>(`/api/v1/graph/episode/${episodeId}${params}`);
  }

  /**
   * Get facet subgraph with connected FacetPoints and Entities.
   * @param datasetId - Optional dataset ID for multi-tenant support
   */
  async getFacetSubgraph(facetId: string, datasetId?: string): Promise<GraphData> {
    const params = datasetId ? `?dataset_id=${datasetId}` : "";
    return this.request<GraphData>(`/api/v1/graph/facet/${facetId}${params}`);
  }

  /**
   * Get episodes overview for Layer 0 navigation.
   * Returns all episodes with aggregated counts.
   */
  async getEpisodesOverview(options?: {
    datasetId?: string;
    limit?: number;
    offset?: number;
  }): Promise<EpisodesOverviewResponse> {
    const params = new URLSearchParams();
    if (options?.datasetId) params.append("dataset_id", options.datasetId);
    if (options?.limit) params.append("limit", options.limit.toString());
    if (options?.offset) params.append("offset", options.offset.toString());
    const queryString = params.toString();
    return this.request<EpisodesOverviewResponse>(
      `/api/v1/graph/episodes${queryString ? `?${queryString}` : ""}`
    );
  }

  /**
   * Get entity network showing all connected Episodes and Facets.
   * Used for Layer 3 Entity network view.
   */
  async getEntityNetwork(entityId: string, datasetId?: string): Promise<EntityNetworkResponse> {
    const params = datasetId ? `?dataset_id=${datasetId}` : "";
    return this.request<EntityNetworkResponse>(`/api/v1/graph/entity/${entityId}/network${params}`);
  }

  // --------------------------------------------------------------------------
  // Settings Management
  // --------------------------------------------------------------------------

  /**
   * Get current system settings
   * 
   * @endpoint GET /api/v1/settings
   * @description Get server-stored configuration info
   * @returns Settings current config (note: some fields may be empty depending on backend implementation)
   * 
   * @note Backend only returns llm and vector_db config
   *       Other configs (embedding, graph_db, etc.) are controlled via env vars
   */
  async getSettings(): Promise<Settings> {
    return this.request<Settings>("/api/v1/settings");
  }

  /**
   * Update system settings
   * 
   * @endpoint POST /api/v1/settings
   * @description Update server config (supports partial updates)
   * @param settings Config to update
   * 
   * Supports updating these configs:
   *   - llm: { provider, model, api_key }
   *   - vector_db: { provider, url, api_key }
   *   - embedding: { provider, model, dimensions, endpoint, api_key }
   * 
   * @example
   * // Update LLM config
   * await apiClient.updateSettings({
   *   llm: { provider: 'openai', model: 'gpt-4o-mini', api_key: 'sk-...' }
   * });
   * 
   * // Update Embedding config
   * await apiClient.updateSettings({
   *   embedding: { provider: 'openai', model: 'text-embedding-3-small', dimensions: 1536 }
   * });
   */
  async updateSettings(settings: Record<string, unknown>): Promise<void> {
    await this.request<void>("/api/v1/settings", {
      method: "POST",
      body: JSON.stringify(settings),
    });
  }

  // --------------------------------------------------------------------------
  // Prompts Management
  // --------------------------------------------------------------------------

  /**
   * Get all Prompts (grouped by category)
   * 
   * @endpoint GET /api/v1/prompts
   */
  async getPrompts(): Promise<PromptCategory[]> {
    return this.request<PromptCategory[]>("/api/v1/prompts");
  }

  /**
   * Get single Prompt
   * 
   * @endpoint GET /api/v1/prompts/{filename}
   */
  async getPrompt(filename: string): Promise<Prompt> {
    return this.request<Prompt>(`/api/v1/prompts/${filename}`);
  }

  /**
   * Update Prompt content
   * 
   * @endpoint PUT /api/v1/prompts/{filename}
   */
  async updatePrompt(filename: string, content: string): Promise<Prompt> {
    return this.request<Prompt>(`/api/v1/prompts/${filename}`, {
      method: "PUT",
      body: JSON.stringify({ content }),
    });
  }

  /**
   * Reset Prompt to default value
   * 
   * @endpoint POST /api/v1/prompts/{filename}/reset
   */
  async resetPrompt(filename: string): Promise<Prompt> {
    return this.request<Prompt>(`/api/v1/prompts/${filename}/reset`, {
      method: "POST",
    });
  }

  /**
   * Reset all Prompts to default values
   * 
   * @endpoint POST /api/v1/prompts/reset-all
   */
  async resetAllPrompts(): Promise<{ reset_count: number; message: string }> {
    return this.request<{ reset_count: number; message: string }>("/api/v1/prompts/reset-all", {
      method: "POST",
    });
  }

  // --------------------------------------------------------------------------
  // Permissions & Access Control
  // --------------------------------------------------------------------------

  async createRole(roleName: string): Promise<{ message: string; role_id: string }> {
    return this.request(`/api/v1/permissions/roles?role_name=${encodeURIComponent(roleName)}`, {
      method: "POST",
    });
  }

  async assignRoleToUser(userId: string, roleId: string): Promise<{ message: string }> {
    return this.request(`/api/v1/permissions/users/${userId}/roles?role_id=${roleId}`, {
      method: "POST",
    });
  }

  async grantDatasetPermission(
    principalId: string,
    datasetIds: string[],
    permissionName: string
  ): Promise<{ message: string }> {
    const params = new URLSearchParams();
    params.set("permission_name", permissionName);
    datasetIds.forEach((id) => params.append("dataset_ids", id));
    return this.request(`/api/v1/permissions/datasets/${principalId}?${params.toString()}`, {
      method: "POST",
    });
  }

  async createTenant(tenantName: string): Promise<{ message: string; tenant_id: string }> {
    return this.request(`/api/v1/permissions/tenants?tenant_name=${encodeURIComponent(tenantName)}`, {
      method: "POST",
    });
  }

  async assignTenantToUser(userId: string, tenantId: string): Promise<{ message: string }> {
    return this.request(`/api/v1/permissions/users/${userId}/tenants?tenant_id=${tenantId}`, {
      method: "POST",
    });
  }

  async selectTenant(tenantId: string | null): Promise<{ message: string }> {
    return this.request("/api/v1/permissions/tenants/select", {
      method: "POST",
      body: JSON.stringify({ tenant_id: tenantId }),
    });
  }

  // --------------------------------------------------------------------------
  // Procedural Memory Extraction
  // --------------------------------------------------------------------------

  /**
   * Extract procedural memories from existing episodic memories
   * 
   * @endpoint POST /api/v1/procedural/extract-from-episodic
   * @description Analyze existing episodic memories and extract procedural knowledge
   * @param options.dataset_id - Filter to specific dataset
   * @param options.episode_ids - Specific episode IDs to process
   * @param options.limit - Maximum episodes to process (default 100)
   * @param options.force_reprocess - Reprocess already processed episodes
   * @param options.dry_run - Preview without writing
   */
  async extractProcedural(options: {
    dataset_id?: string;
    limit?: number;
    force_reprocess?: boolean;
  } = {}): Promise<{
    success: boolean;
    message: string;
    workflow_name: string;
    datasets_submitted: number;
  }> {
    return this.request("/api/v1/procedural/extract-from-episodic", {
      method: "POST",
      body: JSON.stringify({
        dataset_id: options.dataset_id,
        limit: options.limit ?? 100,
        force_reprocess: options.force_reprocess ?? false,
      }),
    });
  }

  // --------------------------------------------------------------------------
  // Maintenance API
  // --------------------------------------------------------------------------

  /**
   * Get Episode quality statistics
   * 
   * @endpoint GET /api/v1/maintenance/episode-quality
   * @description Returns quality metrics for all Episodes including empty and oversized detection
   */
  async getEpisodeQuality(datasetId?: string): Promise<EpisodeQualityResponse> {
    const params = datasetId ? `?dataset_id=${datasetId}` : "";
    return this.request<EpisodeQualityResponse>(`/api/v1/maintenance/episode-quality${params}`);
  }

  /**
   * Run Size Check on specified Episodes
   * 
   * @endpoint POST /api/v1/maintenance/episode-size-check
   * @description Uses LLM to audit Episodes and split/keep based on semantic focus
   */
  async runEpisodeSizeCheck(episodeIds: string[]): Promise<SizeCheckResponse> {
    return this.request<SizeCheckResponse>("/api/v1/maintenance/episode-size-check", {
      method: "POST",
      body: JSON.stringify({ episode_ids: episodeIds }),
    });
  }

  // --------------------------------------------------------------------------
  // Node Deletion API
  // --------------------------------------------------------------------------

  /**
   * Preview node deletion impact (no actual deletion)
   * 
   * @endpoint GET /api/v1/delete/node/{nodeId}/preview
   * @description Returns node info, edge count, and affected neighbor types
   */
  async previewNodeDeletion(
    nodeId: string, 
    datasetId: string
  ): Promise<DeletionPreview> {
    return this.request<DeletionPreview>(
      `/api/v1/delete/node/${nodeId}/preview?dataset_id=${datasetId}`
    );
  }

  /**
   * Delete a single graph node
   * 
   * @endpoint DELETE /api/v1/delete/node/{nodeId}
   * @description Uses DETACH DELETE to remove node and its edges
   * @param nodeId Node ID to delete
   * @param datasetId Dataset ID for permission validation
   * @param cascade If true, cascade delete orphan nodes (hard mode)
   */
  async deleteNode(
    nodeId: string, 
    datasetId: string, 
    cascade: boolean = false
  ): Promise<DeletionResult> {
    return this.request<DeletionResult>(
      `/api/v1/delete/node/${nodeId}?dataset_id=${datasetId}&cascade=${cascade}`,
      { method: "DELETE" }
    );
  }

  /**
   * Delete an Episode node
   * 
   * @endpoint DELETE /api/v1/delete/episode/{episodeId}
   * @description Delete Episode with optional orphan cleanup
   * @param episodeId Episode ID to delete
   * @param datasetId Dataset ID for permission validation
   * @param mode "soft" (default) - delete Episode only; "hard" - also cleanup orphan Facets
   */
  async deleteEpisode(
    episodeId: string, 
    datasetId: string,
    mode: "soft" | "hard" = "soft"
  ): Promise<DeletionResult> {
    return this.request<DeletionResult>(
      `/api/v1/delete/episode/${episodeId}?dataset_id=${datasetId}&mode=${mode}`,
      { method: "DELETE" }
    );
  }

  // --------------------------------------------------------------------------
  // Coreference Resolution
  // --------------------------------------------------------------------------

  /**
   * Get coreference configuration
   *
   * @endpoint GET /api/v1/settings/coreference
   * @description Returns current coreference module configuration
   */
  async getCorefConfig(): Promise<CorefConfig> {
    return this.request<CorefConfig>("/api/v1/settings/coreference");
  }

  /**
   * Update coreference configuration
   *
   * @endpoint POST /api/v1/settings/coreference
   * @description Update coreference settings (partial updates supported)
   * @param config Configuration fields to update
   */
  async updateCorefConfig(config: CorefConfigUpdate): Promise<void> {
    await this.request<void>("/api/v1/settings/coreference", {
      method: "POST",
      body: JSON.stringify(config),
    });
  }

  /**
   * Get coreference module statistics
   *
   * @endpoint GET /api/v1/coreference/stats
   * @description Returns active session count and configuration limits
   * @param includeSessions If true, include list of active sessions
   * @param limit Maximum sessions to return (default: 100)
   */
  async getCorefStats(includeSessions = false, limit = 100): Promise<CorefStats> {
    const params = new URLSearchParams({
      include_sessions: String(includeSessions),
      limit: String(limit),
    });
    return this.request<CorefStats>(`/api/v1/coreference/stats?${params}`);
  }

  /**
   * Reset a coreference session
   *
   * @endpoint POST /api/v1/coreference/sessions/{sessionId}/reset
   * @description Clear accumulated context for a specific session
   * @param sessionId Session ID to reset
   */
  async resetCorefSession(sessionId: string): Promise<{ status: string; session_id: string }> {
    return this.request<{ status: string; session_id: string }>(
      `/api/v1/coreference/sessions/${encodeURIComponent(sessionId)}/reset`,
      { method: "POST" }
    );
  }

  // --------------------------------------------------------------------------
  // Document Deletion (by data_id)
  // --------------------------------------------------------------------------

  /**
   * Delete a document from a dataset
   *
   * @endpoint DELETE /api/v1/delete
   * @param params data_id, dataset_id, mode (soft/hard)
   */
  async deleteDocument(params: DeleteDocumentParams): Promise<DeleteDocumentResponse> {
    const qs = new URLSearchParams({
      data_id: params.data_id,
      dataset_id: params.dataset_id,
    });
    if (params.mode) qs.set("mode", params.mode);
    return this.request<DeleteDocumentResponse>(
      `/api/v1/delete?${qs.toString()}`,
      { method: "DELETE" }
    );
  }

  // --------------------------------------------------------------------------
  // Data Sync
  // --------------------------------------------------------------------------

  /**
   * Trigger data sync
   *
   * @endpoint POST /api/v1/sync
   */
  async syncData(request: SyncRequest = {}): Promise<Record<string, SyncResponse>> {
    return this.request<Record<string, SyncResponse>>("/api/v1/sync", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  /**
   * Get sync status
   *
   * @endpoint GET /api/v1/sync/status
   */
  async getSyncStatus(): Promise<SyncStatusResponse> {
    return this.request<SyncStatusResponse>("/api/v1/sync/status");
  }

  // --------------------------------------------------------------------------
  // Dataset Status & Raw Data
  // --------------------------------------------------------------------------

  /**
   * Get dataset processing status
   *
   * @endpoint GET /api/v1/datasets/status
   */
  async getDatasetsStatus(datasetIds?: string[]): Promise<DatasetStatusResponse> {
    const params = new URLSearchParams();
    datasetIds?.forEach((id) => params.append("dataset", id));
    const qs = params.toString();
    return this.request<DatasetStatusResponse>(
      `/api/v1/datasets/status${qs ? `?${qs}` : ""}`
    );
  }

  /**
   * Get data items for a dataset
   *
   * @endpoint GET /api/v1/datasets/{datasetId}/data
   */
  async getDataItems(datasetId: string): Promise<DataItemInfo[]> {
    return this.request<DataItemInfo[]>(`/api/v1/datasets/${datasetId}/data`);
  }

  /**
   * Download raw data for a document
   *
   * @endpoint GET /api/v1/datasets/{datasetId}/data/{dataId}/raw
   * @returns Blob for file download
   */
  async getDataRaw(datasetId: string, dataId: string): Promise<Blob> {
    const url = `${this.baseUrl}/api/v1/datasets/${datasetId}/data/${dataId}/raw`;
    const headers: HeadersInit = {};
    if (this.token) {
      headers["Authorization"] = `Bearer ${this.token}`;
    }
    const response = await fetch(url, { headers, credentials: "include" });
    if (!response.ok) {
      throw new ServiceFault("DOWNLOAD_FAILED", `Failed to download raw data: ${response.status}`);
    }
    return response.blob();
  }

  // --------------------------------------------------------------------------
  // Manual Node Editing
  // --------------------------------------------------------------------------

  /**
   * Patch a graph node (edit display_only)
   *
   * @endpoint PATCH /api/v1/manual/node
   */
  async patchNode(request: PatchNodeRequest): Promise<PatchNodeResult> {
    return this.request<PatchNodeResult>("/api/v1/manual/node", {
      method: "PATCH",
      body: JSON.stringify(request),
    });
  }

  /**
   * Get manual ingestion schema
   *
   * @endpoint GET /api/v1/manual/schema
   */
  async getManualSchema(): Promise<Record<string, unknown>> {
    return this.request<Record<string, unknown>>("/api/v1/manual/schema");
  }

  // --------------------------------------------------------------------------
  // Granular Prune Operations
  // --------------------------------------------------------------------------

  /**
   * Prune file storage only
   *
   * @endpoint POST /api/v1/prune/data
   */
  async pruneData(): Promise<PruneResponse> {
    return this.request<PruneResponse>("/api/v1/prune/data", {
      method: "POST",
      body: JSON.stringify({ confirm: "DELETE_FILES" }),
    });
  }

  /**
   * Prune system databases selectively
   *
   * @endpoint POST /api/v1/prune/system
   */
  async pruneSystem(options: Partial<Pick<PruneSystemRequest, "graph" | "vector" | "metadata" | "cache">> = {}): Promise<PruneResponse> {
    return this.request<PruneResponse>("/api/v1/prune/system", {
      method: "POST",
      body: JSON.stringify({
        confirm: "DELETE_SYSTEM",
        graph: options.graph ?? true,
        vector: options.vector ?? true,
        metadata: options.metadata ?? true,
        cache: options.cache ?? true,
      }),
    });
  }

  /**
   * Prune all data
   *
   * @endpoint POST /api/v1/prune/all
   */
  async pruneAll(): Promise<PruneResponse> {
    return this.request<PruneResponse>("/api/v1/prune/all", {
      method: "POST",
      body: JSON.stringify({ confirm: "DELETE_ALL_DATA" }),
    });
  }

  // --------------------------------------------------------------------------
  // Authentication - Extended (Forgot/Reset/Verify)
  // --------------------------------------------------------------------------

  /**
   * Request password reset email
   *
   * @endpoint POST /api/v1/auth/forgot-password
   */
  async forgotPassword(email: string): Promise<void> {
    await this.request<void>("/api/v1/auth/forgot-password", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
  }

  /**
   * Reset password with token
   *
   * @endpoint POST /api/v1/auth/reset-password
   */
  async resetPassword(token: string, password: string): Promise<void> {
    await this.request<void>("/api/v1/auth/reset-password", {
      method: "POST",
      body: JSON.stringify({ token, password }),
    });
  }

  /**
   * Request email verification token
   *
   * @endpoint POST /api/v1/auth/request-verify-token
   */
  async requestVerifyToken(email: string): Promise<void> {
    await this.request<void>("/api/v1/auth/request-verify-token", {
      method: "POST",
      body: JSON.stringify({ email }),
    });
  }

  /**
   * Verify email with token
   *
   * @endpoint POST /api/v1/auth/verify
   */
  async verifyEmail(token: string): Promise<void> {
    await this.request<void>("/api/v1/auth/verify", {
      method: "POST",
      body: JSON.stringify({ token }),
    });
  }

  // --------------------------------------------------------------------------
  // Responses API (OpenAI-compatible)
  // --------------------------------------------------------------------------

  /**
   * Send a completion request via Responses API
   *
   * @endpoint POST /api/v1/responses/
   */
  async createResponse(request: ResponsesRequest): Promise<ResponsesResponse> {
    return this.request<ResponsesResponse>("/api/v1/responses/", {
      method: "POST",
      body: JSON.stringify(request),
    });
  }

  // --------------------------------------------------------------------------
  // Enhanced Search (with coreference)
  // --------------------------------------------------------------------------

  /**
   * Execute search with coreference parameters
   */
  async searchWithCoref(options: SearchOptionsWithCoref): Promise<SearchResponse> {
    const payload = {
      recall_mode: options.recall_mode,
      query: options.query,
      datasets: options.datasets,
      dataset_ids: options.dataset_ids,
      system_prompt: options.system_prompt || undefined,
      node_name: options.node_name,
      top_k: options.top_k ?? 10,
      only_context: options.only_context ?? false,
      use_combined_context: options.use_combined_context ?? false,
      wide_search_top_k: options.wide_search_top_k,
      triplet_distance_penalty: options.triplet_distance_penalty,
      verbose: options.verbose ?? false,
      enable_hybrid_search: options.enable_hybrid_search,
      enable_time_bonus: options.enable_time_bonus,
      edge_miss_cost: options.edge_miss_cost,
      hop_cost: options.hop_cost,
      full_number_match_bonus: options.full_number_match_bonus,
      enable_adaptive_weights: options.enable_adaptive_weights,
      display_mode: options.display_mode,
      max_facets_per_episode: options.max_facets_per_episode,
      max_points_per_facet: options.max_points_per_facet,
      coref_enabled: options.coref_enabled,
      coref_session_id: options.coref_session_id,
      coref_new_turn: options.coref_new_turn,
    };

    const rawResponse = await this.request<unknown>("/api/v1/search", {
      method: "POST",
      body: JSON.stringify(payload),
    });

    if (!Array.isArray(rawResponse)) {
      const combined = rawResponse as {
        result: unknown;
        context: Record<string, unknown>;
      };
      let resultContent: string;
      if (combined.result == null) {
        const contextValues = Object.values(combined.context || {}).map((v) =>
          typeof v === "string" ? v : JSON.stringify(v, null, 2)
        );
        resultContent = contextValues.join("\n\n") || "No results found";
      } else if (typeof combined.result === "string") {
        resultContent = combined.result;
      } else {
        resultContent = JSON.stringify(combined.result, null, 2);
      }
      return {
        results: [{
          id: "combined-0",
          content: resultContent,
          score: 1.0,
          source_dataset: "combined",
          metadata: combined.context,
        }],
        total: 1,
        query: options.query,
        recall_mode: options.recall_mode,
      };
    }

    const results: SearchResultItem[] = [];
    for (const dsResult of rawResponse as Array<{
      search_result: unknown;
      dataset_id: string;
      dataset_name: string;
    }>) {
      const searchResult = dsResult.search_result;
      let contentArray: string[];
      if (searchResult == null) {
        contentArray = [];
      } else if (Array.isArray(searchResult)) {
        contentArray = searchResult.map((item) =>
          typeof item === "string" ? item : JSON.stringify(item, null, 2)
        );
      } else if (typeof searchResult === "string") {
        contentArray = [searchResult];
      } else {
        contentArray = [JSON.stringify(searchResult, null, 2)];
      }
      for (const content of contentArray) {
        results.push({
          id: `${dsResult.dataset_id}-${results.length}`,
          content,
          score: 1.0,
          source_dataset: dsResult.dataset_name,
        });
      }
    }

    return {
      results,
      total: results.length,
      query: options.query,
      recall_mode: options.recall_mode,
    };
  }
}

// ============================================================================
// Default client instance
// ============================================================================

export const apiClient = new MflowApiClient();
