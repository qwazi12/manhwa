"use client";

import React, { useEffect, useState } from "react";
import { ScheduleInfo, StudioProject, studioApi } from "@/lib/api";
import { MomentumBadge } from "./MomentumBadge";
import { PacingThrottle } from "./PacingThrottle";
import { StopButton } from "./StopButton";
import { UndoButton } from "./UndoButton";

const STAGES: { key: string; label: string; hint: string }[] = [
  { key: "scrape", label: "1. Scrape", hint: "Download chapter strip images" },
  { key: "split", label: "2. Split", hint: "YOLO panel detector slicing" },
  { key: "describe", label: "3. Describe", hint: "Gemini Vision & Cast Bible" },
  { key: "narrate", label: "4. Script", hint: "Grounded recap narration" },
  { key: "voice", label: "5. Voice", hint: "Gemini Charon / TTS audio lines" },
  { key: "match", label: "6. Match", hint: "DP optimal narration alignment" },
  { key: "render", label: "7. Render", hint: "HyperFrames 1080p Ken Burns video" },
];

export function StudioPanel() {
  const [projects, setProjects] = useState<StudioProject[]>([]);
  const [selected, setSelected] = useState<StudioProject | null>(null);
  const [sched, setSched] = useState<ScheduleInfo | null>(null);
  const [backup, setBackup] = useState<any | null>(null);
  const [loading, setLoading] = useState(true);
  const [err, setErr] = useState("");
  const [undoV, setUndoV] = useState(0);

  async function load() {
    try {
      const [pList, sInfo, bInfo] = await Promise.all([
        studioApi.projects(),
        studioApi.schedule(),
        studioApi.backupStatus(),
      ]);
      setProjects(pList);
      setSched(sInfo);
      setBackup(bInfo);
      if (selected) {
        const found = pList.find((x) => x.project_id === selected.project_id);
        if (found) setSelected(found);
      }
    } catch (e: any) {
      setErr(e.message || String(e));
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    const interval = setInterval(load, 5000);
    return () => clearInterval(interval);
  }, []);

  async function runStage(projId: string, stage: string, auto = false, until = "match") {
    setErr("");
    try {
      await studioApi.run(projId, stage, auto, until);
      await load();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  async function stopProj(projId: string) {
    setErr("");
    try {
      await studioApi.stop(projId);
      await load();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  async function triggerBackup() {
    setErr("");
    try {
      await studioApi.runBackup();
      await load();
    } catch (e: any) {
      setErr(e.message || String(e));
    }
  }

  if (selected) {
    const isRunning = selected.stage_status === "running" || selected.stage_status === "queued";
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
        {/* Detail Header */}
        <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "16px", display: "flex", gap: 16, alignItems: "flex-start", flexWrap: "wrap" }}>
          <button type="button" onClick={() => setSelected(null)} style={{ fontSize: 11 }}>
            ← All Chapters
          </button>
          {selected.poster && (
            <img src={selected.poster} alt="" style={{ width: 64, height: 90, objectFit: "cover", borderRadius: 6 }} />
          )}
          <div style={{ flex: 1, minWidth: 260 }}>
            <h2 style={{ margin: 0, fontSize: 18 }}>{selected.title}</h2>
            <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 2 }}>
              Series: <b>{selected.series_slug || "—"}</b> · Chapter {selected.chapter_number} · Studio #{selected.id}
            </div>
            <div style={{ marginTop: 6 }}>
              <MomentumBadge momentum={selected.momentum} />
            </div>
            <div style={{ marginTop: 6, fontSize: 12, color: selected.stage_status === "error" ? "var(--red)" : isRunning ? "var(--yellow)" : "var(--text)" }}>
              {isRunning ? "⏳ " : selected.stage_status === "done" ? "✓ " : selected.stage_status === "stopped" ? "■ " : ""}
              {selected.stage_message || "Idle"}
            </div>
          </div>

          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            {isRunning && (
              <StopButton immediate what="Stop activity immediately; finished stages are kept" onStop={() => stopProj(selected.project_id)} />
            )}
            <UndoButton
              scope={`studio:${selected.project_id}`}
              version={`${undoV}-${selected.updated_at}`}
              onUndone={() => {
                setUndoV((v) => v + 1);
                load();
              }}
            />
            <a href={`/chapter/${selected.project_id}`} style={{ fontSize: 11, padding: "6px 12px", background: "var(--chip)", borderRadius: 6, color: "var(--text)", textDecoration: "none" }}>
              Open in Board ↗
            </a>
          </div>
        </div>

        {/* 7-Stage Pipeline Stepper */}
        <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "16px" }}>
          <h3 style={{ margin: "0 0 12px", fontSize: 14 }}>🎬 7-Stage Recap Production Pipeline</h3>
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(140px, 1fr))", gap: 8 }}>
            {STAGES.map((s, idx) => {
              const done = selected.has[s.key as keyof typeof selected.has] || false;
              const active = selected.stage === s.key;
              return (
                <div
                  key={s.key}
                  style={{
                    padding: "10px",
                    borderRadius: 8,
                    border: `1px solid ${active ? "var(--accent)" : "var(--border)"}`,
                    background: done ? "rgba(34, 197, 94, 0.08)" : "var(--row)",
                    display: "flex",
                    flexDirection: "column",
                    justifyContent: "space-between",
                  }}
                >
                  <div>
                    <div style={{ fontSize: 12, fontWeight: 700, color: done ? "#4ade80" : "var(--text)" }}>
                      {done ? "✓ " : ""}{s.label}
                    </div>
                    <div style={{ fontSize: 10, color: "var(--muted)", margin: "4px 0 8px" }}>{s.hint}</div>
                  </div>

                  <div style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
                    <button
                      type="button"
                      disabled={isRunning}
                      onClick={() => runStage(selected.project_id, s.key, false)}
                      style={{ fontSize: 10, padding: "2px 8px" }}
                      title={`Run just ${s.label}`}
                    >
                      {done ? "Re-run" : "Run"}
                    </button>
                    {idx < STAGES.length - 1 && (
                      <button
                        type="button"
                        disabled={isRunning}
                        onClick={() => runStage(selected.project_id, s.key, true, "render")}
                        style={{ fontSize: 10, padding: "2px 8px" }}
                        title="Auto-chain from here all the way through to final video"
                      >
                        ↻ from here
                      </button>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        </div>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      {/* Studio Header & Disaster Recovery Status */}
      <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "16px", display: "flex", justifyContent: "space-between", alignItems: "center", flexWrap: "wrap", gap: 12 }}>
        <div>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <span style={{ fontSize: 20 }}>🎬</span>
            <h1 style={{ margin: 0, fontSize: 18 }}>LongForm Studio</h1>
            <span style={{ fontSize: 10, padding: "2px 8px", background: "rgba(59, 130, 246, 0.2)", color: "#93c5fd", borderRadius: 10, fontWeight: 700 }}>
              7-Stage Production Pipeline
            </span>
          </div>
          <div style={{ fontSize: 11, color: "var(--muted)", marginTop: 4 }}>
            Granular stage control, drift-free mathematical pacing, online database backups & atomic render safety
          </div>
        </div>

        <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
          {backup && (
            <div style={{ fontSize: 11, color: backup.is_stale ? "var(--yellow)" : "#4ade80", background: "var(--chip)", padding: "4px 10px", borderRadius: 6 }}>
              🛡️ {backup.local_copies_count} Local Backups ({backup.is_stale ? "Stale" : "Healthy"})
            </div>
          )}
          <button type="button" onClick={triggerBackup} style={{ fontSize: 11, padding: "5px 12px" }}>
            📸 Take DB Snapshot
          </button>
        </div>
      </div>

      {err && (
        <div style={{ background: "rgba(220, 38, 38, 0.15)", border: "1px solid rgba(239, 68, 68, 0.4)", borderRadius: 8, padding: "10px 14px", color: "#fca5a5", fontSize: 12 }}>
          {err}
        </div>
      )}

      {/* Pacing Throttle & Velocity Engine */}
      {sched && <PacingThrottle sched={sched} onSaved={setSched} />}

      {/* Chapter Breakdown Projects List */}
      <div style={{ background: "var(--panel)", border: "1px solid var(--border)", borderRadius: 10, padding: "16px" }}>
        <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", marginBottom: 12 }}>
          <h2 style={{ margin: 0, fontSize: 14 }}>📹 Recaps in Studio ({projects.length})</h2>
          <UndoButton
            scope="queue"
            version={undoV}
            onUndone={() => {
              setUndoV((v) => v + 1);
              load();
            }}
          />
        </div>

        {projects.length === 0 ? (
          <div style={{ fontSize: 12, color: "var(--muted)", padding: "20px 0", textAlign: "center" }}>
            No chapters imported yet. Paste a link on Home to begin!
          </div>
        ) : (
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(280px, 1fr))", gap: 12 }}>
            {projects.map((p) => {
              const isRunning = p.stage_status === "running" || p.stage_status === "queued";
              return (
                <div
                  key={p.project_id}
                  onClick={() => setSelected(p)}
                  style={{
                    background: "var(--row)",
                    border: "1px solid var(--border)",
                    borderRadius: 8,
                    padding: "12px",
                    cursor: "pointer",
                    display: "flex",
                    gap: 12,
                    alignItems: "center",
                    transition: "border-color 0.15s ease",
                  }}
                >
                  {p.poster ? (
                    <img src={p.poster} alt="" style={{ width: 48, height: 68, objectFit: "cover", borderRadius: 4, flexShrink: 0 }} />
                  ) : (
                    <div style={{ width: 48, height: 68, background: "var(--chip)", borderRadius: 4, flexShrink: 0 }} />
                  )}

                  <div style={{ flex: 1, minWidth: 0, display: "flex", flexDirection: "column", gap: 3 }}>
                    <div style={{ fontSize: 12, fontWeight: 700, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                      {p.title}
                    </div>
                    <MomentumBadge momentum={p.momentum} compact />
                    <div style={{ fontSize: 10, color: isRunning ? "var(--yellow)" : p.stage_status === "done" ? "#4ade80" : "var(--muted)" }}>
                      {isRunning ? "⏳ " : ""}{p.stage} — {p.stage_status}
                    </div>
                    {p.cost_usd > 0 && (
                      <div style={{ fontSize: 10, color: "var(--muted)" }}>
                        spent ${p.cost_usd.toFixed(2)}
                      </div>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
        )}
      </div>
    </div>
  );
}
