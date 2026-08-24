"use client";

/**
 * DatabaseConfigStep Component
 *
 * Configuration step for vector database settings.
 * Editable form for vector_db provider + read-only status for relational/graph DBs.
 */

import React, { useState, useCallback } from "react";
import { cn } from "@/lib/utils";
import { Eye, EyeOff, AlertCircle, CheckCircle2, Info } from "lucide-react";
import type { VectorDBFormData } from "@/hooks/use-config-wizard";
import type { DetailedHealthResponse, ProbeKey } from "@/types";

// ============================================================================
// Types
// ============================================================================

export interface DatabaseConfigStepProps {
  healthData?: DetailedHealthResponse;
  isLoading?: boolean;
  formData?: Partial<VectorDBFormData>;
  onFormChange?: (data: Partial<VectorDBFormData>) => void;
  isSaving?: boolean;
  error?: string | null;
  className?: string;
}

// ============================================================================
// Vector DB Providers
// ============================================================================

interface VectorDBProvider {
  id: string;
  name: string;
  requiresUrl: boolean;
  requiresApiKey: boolean;
  defaultUrl?: string;
  hint: string;
}

const VECTOR_DB_PROVIDERS: VectorDBProvider[] = [
  {
    id: "lancedb",
    name: "LanceDB (Local)",
    requiresUrl: false,
    requiresApiKey: false,
    hint: "Embedded, zero-config local vector database",
  },
  {
    id: "chromadb",
    name: "ChromaDB",
    requiresUrl: true,
    requiresApiKey: false,
    defaultUrl: "http://localhost:8000",
    hint: "Open-source embedding database",
  },
  {
    id: "pgvector",
    name: "pgvector (PostgreSQL)",
    requiresUrl: true,
    requiresApiKey: false,
    defaultUrl: "postgresql://user:pass@localhost:5432/mflow",
    hint: "PostgreSQL extension for vector similarity search",
  },
  {
    id: "qdrant",
    name: "Qdrant",
    requiresUrl: true,
    requiresApiKey: true,
    defaultUrl: "http://localhost:6333",
    hint: "Open-source vector database, local server or Qdrant Cloud",
  },
];

// ============================================================================
// Connection Status Component
// ============================================================================

function ConnectionStatus({ healthData, probeKey, label }: { healthData?: DetailedHealthResponse; probeKey: ProbeKey; label: string }) {
  if (!healthData?.probes?.[probeKey]) return null;

  const probe = healthData.probes[probeKey];
  const isConnected = probe.verdict === "up";
  const isWarning = probe.verdict === "warn";

  return (
    <div
      className={cn(
        "flex items-center gap-2 px-3 py-2 rounded-lg text-[12px]",
        isConnected && "bg-emerald-950/20 text-emerald-400",
        isWarning && "bg-amber-950/20 text-amber-400",
        !isConnected && !isWarning && "bg-red-950/20 text-red-400"
      )}
    >
      {isConnected ? (
        <CheckCircle2 size={14} />
      ) : (
        <AlertCircle size={14} />
      )}
      <span className="font-medium">
        {label}: {isConnected ? "Connected" : isWarning ? "Degraded" : "Not connected"}
      </span>
      {probe.backend !== "unknown" && (
        <>
          <span className="text-zinc-600">·</span>
          <span className="text-zinc-400">{probe.backend}</span>
        </>
      )}
      {probe.latency_ms > 0 && (
        <>
          <span className="text-zinc-600">·</span>
          <span className="text-zinc-400">{probe.latency_ms}ms</span>
        </>
      )}
    </div>
  );
}

// ============================================================================
// Form Field Component (matches LLMConfigStep)
// ============================================================================

interface FieldProps {
  label: string;
  children: React.ReactNode;
  hint?: string;
  required?: boolean;
}

function Field({ label, children, hint, required }: FieldProps) {
  return (
    <div className="space-y-1.5">
      <label className="flex items-center gap-1 text-[12px] font-medium text-zinc-300">
        {label}
        {required && <span className="text-red-400">*</span>}
      </label>
      {children}
      {hint && <p className="text-[11px] text-zinc-500">{hint}</p>}
    </div>
  );
}

// ============================================================================
// Main Component
// ============================================================================

export function DatabaseConfigStep({
  healthData,
  isLoading = false,
  formData,
  onFormChange,
  isSaving = false,
  error,
  className,
}: DatabaseConfigStepProps) {
  const [showApiKey, setShowApiKey] = useState(false);

  const currentProvider = VECTOR_DB_PROVIDERS.find((p) => p.id === formData?.provider);

  const handleProviderChange = useCallback(
    (providerId: string) => {
      const provider = VECTOR_DB_PROVIDERS.find((p) => p.id === providerId);
      onFormChange?.({
        ...formData,
        provider: providerId,
        url: provider?.defaultUrl || "",
        apiKey: provider?.requiresApiKey ? formData?.apiKey : "",
      });
    },
    [formData, onFormChange]
  );

  const handleChange = useCallback(
    (field: keyof VectorDBFormData, value: string) => {
      onFormChange?.({ ...formData, [field]: value });
    },
    [formData, onFormChange]
  );

  const inputClass = cn(
    "w-full px-3 py-2 rounded-lg text-[13px]",
    "bg-zinc-900 border border-zinc-700",
    "text-zinc-100 placeholder-zinc-500",
    "focus:outline-none focus:ring-2 focus:ring-zinc-500 focus:border-transparent",
    "disabled:opacity-50 disabled:cursor-not-allowed"
  );

  return (
    <div className={cn("space-y-6", className)}>
      {/* Header */}
      <div>
        <h3 className="text-[18px] font-semibold text-zinc-100">
          Vector Database Configuration
        </h3>
        <p className="text-[13px] text-zinc-500 mt-1">
          Configure the vector database for semantic search and embeddings storage
        </p>
      </div>

      {/* Connection Status */}
      <div className="space-y-2">
        <ConnectionStatus healthData={healthData} probeKey="vector_db" label="Vector DB" />
        <ConnectionStatus healthData={healthData} probeKey="relational_db" label="Relational DB" />
        <ConnectionStatus healthData={healthData} probeKey="graph_db" label="Graph DB" />
      </div>

      {/* Error */}
      {error && (
        <div className="flex items-center gap-2 px-3 py-2 rounded-lg bg-red-950/20 text-red-400 text-[12px]">
          <AlertCircle size={14} />
          <span>{error}</span>
        </div>
      )}

      {/* Vector DB Form */}
      <div className="space-y-5">
        <div className="grid grid-cols-1 sm:grid-cols-2 gap-4">
          <Field label="Provider" required hint={currentProvider?.hint}>
            <select
              value={formData?.provider || "lancedb"}
              onChange={(e) => handleProviderChange(e.target.value)}
              disabled={isSaving}
              className={inputClass}
            >
              {VECTOR_DB_PROVIDERS.map((p) => (
                <option key={p.id} value={p.id}>
                  {p.name}
                </option>
              ))}
            </select>
          </Field>

          {/* URL - show for providers that need it */}
          {currentProvider?.requiresUrl && (
            <Field label="Connection URL" required>
              <input
                type="text"
                value={formData?.url || ""}
                onChange={(e) => handleChange("url", e.target.value)}
                placeholder={currentProvider.defaultUrl || "http://localhost:8000"}
                disabled={isSaving}
                className={inputClass}
              />
            </Field>
          )}
        </div>

        {/* API Key - show for providers that need it */}
        {currentProvider?.requiresApiKey && (
          <Field label="API Key" hint="Optional for local instances">
            <div className="relative">
              <input
                type={showApiKey ? "text" : "password"}
                value={formData?.apiKey || ""}
                onChange={(e) => handleChange("apiKey", e.target.value)}
                placeholder="Enter your API key"
                disabled={isSaving}
                className={cn(inputClass, "pr-10")}
              />
              <button
                type="button"
                onClick={() => setShowApiKey(!showApiKey)}
                className="absolute right-2 top-1/2 -translate-y-1/2 p-1 text-zinc-500 hover:text-zinc-300"
                tabIndex={-1}
              >
                {showApiKey ? <EyeOff size={16} /> : <Eye size={16} />}
              </button>
            </div>
          </Field>
        )}
      </div>

      {/* Info: Other databases */}
      <div className="flex items-start gap-3 p-3 rounded-lg bg-zinc-900/50 border border-zinc-800">
        <Info size={16} className="text-zinc-500 shrink-0 mt-0.5" />
        <div className="text-[12px] text-zinc-500">
          <p className="text-zinc-400 font-medium">Relational & Graph Databases</p>
          <p className="mt-1">
            Relational (SQLite/PostgreSQL) and Graph (Kuzu/Neo4j) databases are
            configured via environment variables. M-Flow works out-of-the-box
            with SQLite + Kuzu for local development.
          </p>
        </div>
      </div>
    </div>
  );
}

DatabaseConfigStep.displayName = "DatabaseConfigStep";

export default DatabaseConfigStep;
