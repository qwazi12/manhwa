"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ConfirmButton, useAct } from "./ui";

type Job = { id: string; kind: string; name: string; project?: string; status: string; stage?: string; pct?: number | null; msg?: string; elapsed?: number | null };
const NEEDS_RESUME = ["budget_paused", "interrupted", "paused", "pausing", "cancelled"];

const mins = (s?: number | null) => (s == null ? "" : s >= 60 ? `${Math.floor(s / 60)}m ${s % 60}s` : `${s}s`);

/** Everything running or waiting, on every page — one compact line per job,
 *  like Scrapper's jobs bar (owner, 2026-10-06: "why multiple progress bars?").
 *  No bars here: ⏳ running · ⏸ next / waiting · ⚠ needs you. The full progress
 *  is on the chapter's page. */
export default function JobsBar({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useApi<{ jobs: Job[] }>("/api/jobsbar?failed=1", 3000);
  const act = useAct();
  const [hidden, setHidden] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("jobsbar-hidden") || "[]"); } catch { return []; } });
  const hide = (id: string) => { const h = [...hidden, id].slice(-50); setHidden(h); try { localStorage.setItem("jobsbar-hidden", JSON.stringify(h)); } catch {} };
  const jobs = (data?.jobs || []).filter((j) => !(j.status === "error" && hidden.includes(j.id)));
  if (!jobs.length) return null;
  const control = (id: string, action: string) =>
    act(async () => { await api("/api/jobs/control", { job_id: id, action }); await reload(); onChange?.(); });
  const icon = (j: Job) => j.status === "running" ? "⏳" : j.status === "error" ? "⚠" : NEEDS_RESUME.includes(j.status) ? "⏸" : "⏸";
  const word = (j: Job) => {
    if (j.status === "running") return `${j.stage || "working"}${j.pct != null ? ` · ${j.pct}%` : ""}${j.elapsed != null ? ` · ${mins(j.elapsed)}` : ""}`;
    if (j.status === "queued") return "next";
    if (j.status === "held") return "waiting its turn";
    if (j.status === "in_queue") return `waiting to render · ${j.msg || ""}`;
    if (j.status === "budget_paused") return "paused by a daily limit — resumes by itself";
    if (j.status === "interrupted") return "cut off by a restart — resumes by itself";
    if (j.status === "paused" || j.status === "pausing") return "paused";
    if (j.status === "error") return `render failed${j.msg ? " · " + j.msg : ""}`;
    return j.status.replace("_", " ");
  };
  return (
    <div className="jobsbar" aria-live="polite">
      {jobs.map((j) => {
        const label = <>{j.kind === "autopilot" ? "🤖 " : j.kind === "finalize" || j.kind === "render" ? "🎬 " : ""}{j.project ? <Link href={`/chapter/${encodeURIComponent(j.project)}`}>{j.name}</Link> : j.name}</>;
        return (
          <div className={`job${j.status === "error" ? " err" : ""}`} key={j.id}>
            <span className="jl"><span aria-hidden>{icon(j)}</span> <b>{label}</b></span>
            <span className="muted jm">{word(j)}</span>
            <span className="row" style={{ gap: 4 }}>
              {j.status === "running" && <button className="sm ghost" aria-label="Pause" onClick={() => control(j.id, "pause")}>⏸</button>}
              {(j.status === "paused" || j.status === "pausing") && <button className="sm" onClick={() => control(j.id, "resume")}>▶ Resume</button>}
              {["budget_paused", "interrupted", "cancelled"].includes(j.status) && (
                <button className="sm" onClick={() => act(async () => { await api("/api/jobs/resume", { job_id: j.id }); await reload(); onChange?.(); }, "Resumed")}>▶ Resume</button>)}
              {j.status === "in_queue" && <button className="sm ghost" aria-label="Take out of the queue" onClick={() => act(async () => { await api("/api/render-queue/remove", { id: j.id }); await reload(); }, "Taken out of the queue")}>✕</button>}
              {j.status === "error" && <button className="sm ghost" aria-label="Dismiss" onClick={() => hide(j.id)}>✕</button>}
              {["running", "queued", "held", "paused", "pausing"].includes(j.status) && <ConfirmButton className="sm danger" confirm="Tap to stop" onConfirm={() => control(j.id, "stop")}>■</ConfirmButton>}
            </span>
          </div>
        );
      })}
    </div>
  );
}
