"use client";

import React, { useEffect, useState } from "react";
import { studioApi, UndoStep } from "@/lib/api";

export function UndoButton({
  scope,
  version,
  onUndone,
  compact = false,
}: {
  scope: string;
  version?: unknown;
  onUndone?: (label: string) => void;
  compact?: boolean;
}) {
  const [top, setTop] = useState<UndoStep | null>(null);
  const [busy, setBusy] = useState(false);
  const [err, setErr] = useState("");

  async function load() {
    try {
      const res = await studioApi.undoStack(scope);
      setTop(res.stack[0] || null);
    } catch {
      setTop(null);
    }
  }

  useEffect(() => {
    load();
  }, [scope, version]);

  async function undo() {
    if (!top) return;
    setBusy(true);
    setErr("");
    try {
      const r = await studioApi.undo(scope);
      setTop(r.stack[0] || null);
      onUndone?.(r.undone);
    } catch (e: any) {
      setErr(e.message || String(e));
      load();
    } finally {
      setBusy(false);
    }
  }

  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
      <button
        type="button"
        onClick={undo}
        disabled={!top || busy}
        title={top ? `Undo: ${top.label}` : "Nothing to undo"}
        style={{
          fontSize: 11,
          padding: "5px 10px",
          maxWidth: compact ? 160 : 280,
          overflow: "hidden",
          textOverflow: "ellipsis",
          whiteSpace: "nowrap",
          opacity: top ? 1 : 0.5,
        }}
      >
        ↶ {busy ? "Undoing…" : top ? `Undo: ${top.label}` : "Undo"}
      </button>
      {err && <span style={{ fontSize: 10, color: "var(--red)" }}>{err}</span>}
    </span>
  );
}
