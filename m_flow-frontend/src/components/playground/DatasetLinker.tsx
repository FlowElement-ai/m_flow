"use client";

import React, { useState, useEffect } from "react";
import { X, Link2, Edit3, Database, Loader2, Check, Plus } from "lucide-react";
import type { PersonInFrame } from "./types";

interface DatasetLinkerProps {
  person: PersonInFrame;
  sessionId: string;
  apiBase: string;
  headers: () => Record<string, string>;
  onClose: () => void;
  onLinked: () => void;
}

interface DatasetOption {
  id: string;
  name: string;
}

export function DatasetLinker({ person, sessionId, apiBase, headers, onClose, onLinked }: DatasetLinkerProps) {
  const [datasets, setDatasets] = useState<DatasetOption[]>([]);
  const [loading, setLoading] = useState(true);
  const [selectedId, setSelectedId] = useState<string>("");
  const [saving, setSaving] = useState(false);
  const [renaming, setRenaming] = useState(false);
  const [newName, setNewName] = useState(person.name || "");
  const [error, setError] = useState<string | null>(null);
  const [creating, setCreating] = useState(false);
  const [createName, setCreateName] = useState(
    `${(person.name || "User").replace(/\s+/g, "_")}_memory`
  );

  useEffect(() => {
    (async () => {
      try {
        const res = await fetch(`${apiBase}/api/v1/datasets`, { headers: headers() });
        if (res.ok) {
          const data = await res.json();
          const list = Array.isArray(data) ? data : data.datasets || [];
          setDatasets(list.map((d: any) => ({ id: String(d.id), name: d.name })));
        }
      } catch { /* ignore */ }
      setLoading(false);
    })();
  }, [apiBase, headers]);

  async function handleLink() {
    if (!selectedId || !person.face_registered_id) return;
    setSaving(true);
    setError(null);
    try {
      const dsName = datasets.find(d => d.id === selectedId)?.name || "";
      const res = await fetch(`${apiBase}/api/v1/playground/link-face`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          face_registered_id: person.face_registered_id,
          dataset_id: selectedId,
          display_name: person.name || dsName,
        }),
      });
      if (!res.ok) { setError(`Link failed (${res.status})`); return; }
      const data = await res.json();
      if (!data.ok) { setError(data.error || "Link failed"); return; }
      onLinked();
      onClose();
    } catch { setError("Network error"); } finally { setSaving(false); }
  }

  async function handleCreateAndLink() {
    if (!createName.trim() || !person.face_registered_id) return;
    setCreating(true);
    setError(null);
    try {
      // Step 1: Create dataset
      const createRes = await fetch(`${apiBase}/api/v1/datasets`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({ name: createName.trim() }),
      });
      if (!createRes.ok) { setError(`Create dataset failed (${createRes.status})`); return; }
      const ds = await createRes.json();
      const dsId = String(ds.id);

      // Step 2: Link face to the new dataset
      const linkRes = await fetch(`${apiBase}/api/v1/playground/link-face`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          face_registered_id: person.face_registered_id,
          dataset_id: dsId,
          display_name: person.name || createName.trim(),
        }),
      });
      if (!linkRes.ok) { setError(`Link failed (${linkRes.status})`); return; }

      onLinked();
      onClose();
    } catch { setError("Network error"); } finally { setCreating(false); }
  }

  async function handleRename() {
    if (!newName.trim() || !person.face_registered_id) return;
    setRenaming(true);
    setError(null);
    try {
      const res = await fetch(`${apiBase}/api/v1/playground/rename-person`, {
        method: "POST",
        headers: headers(),
        body: JSON.stringify({
          face_registered_id: person.face_registered_id,
          new_name: newName.trim(),
          session_id: sessionId,
        }),
      });
      if (!res.ok) { setError(`Rename failed (${res.status})`); return; }
      const data = await res.json();
      if (!data.ok) { setError(data.error || "Rename failed"); return; }
      onLinked();
      onClose();
    } catch { setError("Network error"); } finally { setRenaming(false); }
  }

  return (
    <div className="fixed inset-0 bg-black/60 flex items-center justify-center z-50" onClick={onClose}>
      <div
        className="bg-[#141414] border border-[#2a2a2a] rounded-xl w-[420px] max-h-[80vh] overflow-hidden"
        onClick={e => e.stopPropagation()}
      >
        {/* Header */}
        <div className="flex items-center justify-between px-5 py-4 border-b border-[#2a2a2a]">
          <div className="flex items-center gap-2">
            <Link2 size={16} className="text-[#6b8afd]" />
            <span className="text-sm font-medium text-[#e0e0e0]">Manage Person Link</span>
          </div>
          <button onClick={onClose} className="text-[#808080] hover:text-[#e0e0e0] transition-colors">
            <X size={16} />
          </button>
        </div>

        <div className="p-5 space-y-5">
          {/* Person info */}
          <div className="flex items-center gap-3 p-3 bg-[#0a0a0a] rounded-lg border border-[#2a2a2a]">
            <div className="w-10 h-10 rounded-full bg-[#1e1e1e] border border-[#2a2a2a] flex items-center justify-center text-[#808080] text-sm font-medium overflow-hidden flex-shrink-0">
              {person.avatar ? (
                <img src={`data:image/jpeg;base64,${person.avatar}`} alt="" className="w-full h-full object-cover" />
              ) : (
                (person.name || "?")[0]
              )}
            </div>
            <div>
              <div className="text-sm text-[#e0e0e0]">{person.name || "Unknown"}</div>
              <div className="text-[10px] text-[#808080]">
                Face #{person.face_registered_id}
                {(person.dataset_ids?.length ?? 0) > 0 && ` · ${person.dataset_ids!.length} linked`}
              </div>
            </div>
          </div>

          {/* Rename section */}
          <div>
            <div className="text-xs text-[#808080] mb-2 flex items-center gap-1.5">
              <Edit3 size={11} />
              <span>Rename</span>
            </div>
            <div className="flex gap-2">
              <input
                value={newName}
                onChange={e => setNewName(e.target.value)}
                placeholder="Enter new name"
                className="flex-1 bg-[#0a0a0a] border border-[#2a2a2a] rounded-lg px-3 py-2 text-sm text-[#e0e0e0] placeholder-[#404040] focus:outline-none focus:border-[#6b8afd]"
              />
              <button
                onClick={handleRename}
                disabled={renaming || !newName.trim() || newName === person.name}
                className="px-3 py-2 rounded-lg bg-[#1e1e1e] border border-[#2a2a2a] text-xs text-[#e0e0e0] hover:border-[#6b8afd] disabled:opacity-30 transition-colors"
              >
                {renaming ? <Loader2 size={12} className="animate-spin" /> : <Check size={12} />}
              </button>
            </div>
          </div>

          {/* Dataset linking section */}
          <div>
            <div className="text-xs text-[#808080] mb-2 flex items-center gap-1.5">
              <Database size={11} />
              <span>Link Dataset</span>
            </div>
            {loading ? (
              <div className="flex items-center justify-center py-4">
                <Loader2 size={16} className="animate-spin text-[#808080]" />
              </div>
            ) : datasets.length === 0 ? (
              <div className="space-y-3">
                <div className="text-xs text-[#404040] text-center py-1">No datasets available</div>
                <div className="flex gap-2">
                  <input
                    value={createName}
                    onChange={e => setCreateName(e.target.value)}
                    placeholder="Dataset name"
                    className="flex-1 bg-[#0a0a0a] border border-[#2a2a2a] rounded-lg px-3 py-2 text-sm text-[#e0e0e0] placeholder-[#404040] focus:outline-none focus:border-[#6b8afd]"
                  />
                  <button
                    onClick={handleCreateAndLink}
                    disabled={creating || !createName.trim()}
                    className="px-3 py-2 rounded-lg bg-[#6b8afd] text-white text-xs font-medium hover:bg-[#5a7ae8] disabled:opacity-30 transition-colors flex items-center gap-1.5"
                  >
                    {creating ? <Loader2 size={12} className="animate-spin" /> : <span className="flex items-center gap-1"><Plus size={12} /> Create & Link</span>}
                  </button>
                </div>
              </div>
            ) : (
              <div className="space-y-2">
                <div className="max-h-[200px] overflow-y-auto space-y-1">
                  {datasets.map(ds => (
                    <button
                      key={ds.id}
                      onClick={() => setSelectedId(ds.id === selectedId ? "" : ds.id)}
                      className={`w-full text-left px-3 py-2 rounded-lg text-xs transition-colors ${
                        ds.id === selectedId
                          ? "bg-[#6b8afd]/10 border border-[#6b8afd]/30 text-[#6b8afd]"
                          : "bg-[#0a0a0a] border border-transparent hover:border-[#2a2a2a] text-[#e0e0e0]"
                      }`}
                    >
                      <div className="truncate">{ds.name}</div>
                      <div className="text-[#808080] text-[10px] truncate mt-0.5">{ds.id}</div>
                    </button>
                  ))}
                </div>
                <div className="pt-2 border-t border-[#1e1e1e]">
                  <div className="text-[10px] text-[#404040] mb-1.5">Or create new</div>
                  <div className="flex gap-2">
                    <input
                      value={createName}
                      onChange={e => setCreateName(e.target.value)}
                      placeholder="New dataset name"
                      className="flex-1 bg-[#0a0a0a] border border-[#2a2a2a] rounded-lg px-3 py-1.5 text-xs text-[#e0e0e0] placeholder-[#404040] focus:outline-none focus:border-[#6b8afd]"
                    />
                    <button
                      onClick={handleCreateAndLink}
                      disabled={creating || !createName.trim()}
                      className="px-2.5 py-1.5 rounded-lg bg-[#1e1e1e] border border-[#2a2a2a] text-[10px] text-[#e0e0e0] hover:border-[#6b8afd] disabled:opacity-30 transition-colors flex items-center gap-1"
                    >
                      {creating ? <Loader2 size={10} className="animate-spin" /> : <span className="flex items-center gap-1"><Plus size={10} /> Create</span>}
                    </button>
                  </div>
                </div>
              </div>
            )}
          </div>
        </div>

        {/* Error + Footer */}
        {error && (
          <div className="mx-5 mb-0 px-3 py-2 rounded-lg bg-red-500/10 border border-red-500/20 text-red-400 text-xs">
            {error}
          </div>
        )}
        <div className="px-5 py-4 border-t border-[#2a2a2a] flex justify-end gap-2">
          <button
            onClick={onClose}
            className="px-4 py-2 rounded-lg text-xs text-[#808080] hover:text-[#e0e0e0] transition-colors"
          >
            Cancel
          </button>
          <button
            onClick={handleLink}
            disabled={saving || !selectedId}
            className="px-4 py-2 rounded-lg bg-[#6b8afd] text-white text-xs font-medium hover:bg-[#5a7ae8] disabled:opacity-30 transition-colors"
          >
            {saving ? <Loader2 size={12} className="animate-spin" /> : "Confirm Link"}
          </button>
        </div>
      </div>
    </div>
  );
}
