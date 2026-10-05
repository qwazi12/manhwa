"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { ConfirmButton, useAct } from "./ui";

type Job = { id: string; kind: string; name: string; project?: string; status: string; stage?: string; pct?: number | null; msg?: string; elapsed?: number };
const WAITING = ["budget_paused", "interrupted", "paused", "pausing", "cancelled"];

/** Everything running, on every page, with pause / resume / stop. */
export default function JobsBar({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useApi<{ jobs: Job[] }>("/api/jobsbar?failed=1", 5000);
  const act = useAct();
  const [hidden, setHidden] = useState<string[]>(() => { try { return JSON.parse(localStorage.getItem("jobsbar-hidden") || "[]"); } catch { return []; } });
  const hide = (id: string) => { const h = [...hidden, id].slice(-50); setHidden(h); try { localStorage.setItem("jobsbar-hidden", JSON.stringify(h)); } catch {} };
  const jobs = (data?.jobs || []).filter((j) => !(j.status === "error" && hidden.includes(j.id)));
  if (!jobs.length) return null;
  const control = (id: string, action: string) =>
    act(async () => { await api("/api/jobs/control", { job_id: id, action }); await reload(); onChange?.(); });
  return (
    <div className="jobsbar" aria-live="polite">
      {jobs.map((j) => {
        if (j.status === "in_queue") return (
          <div className="job" key={j.id}>
            <b>🎬 {j.name}</b>
            <span className="muted">waiting in the render queue · {j.msg}</span>
            {j.project && <Link className="btn sm ghost" href={`/chapter/${encodeURIComponent(j.project)}`}>Open</Link>}
            <button className="sm ghost" aria-label="Take out of the queue" onClick={() => act(async () => { await api("/api/render-queue/remove", { id: j.id }); await reload(); }, "Taken out of the queue")}>✕</button>
          </div>
        );
        if (j.status === "error") return (
          <div className="job" key={j.id} style={{ borderColor: "var(--red)" }}>
            <b>🎬 {j.name}</b>
            <span style={{ color: "var(--red)" }}>Render failed{j.msg ? " · " + j.msg : ""}</span>
            {j.project && <Link className="btn sm" href={`/chapter/${encodeURIComponent(j.project)}`}>Open</Link>}
            <button className="sm ghost" aria-label="Dismiss" onClick={() => hide(j.id)}>✕</button>
          </div>
        );
        const waiting = WAITING.includes(j.status);
        return (
          <div className="job" key={j.id}>
            <b>{j.kind === "autopilot" ? "🤖 " : j.kind === "finalize" ? "🎬 " : ""}{j.project ? <Link href={`/chapter/${encodeURIComponent(j.project)}`}>{j.name}</Link> : j.name}</b>
            <span className="muted">{waiting ? `waiting (${j.status.replace("_", " ")})` : `${j.stage || j.status}${j.msg ? " · " + j.msg : ""}`}</span>
            {!waiting && <span className="prog"><i style={{ width: `${j.pct ?? 5}%` }} /></span>}
            {waiting ? (
              <button className="sm" onClick={() => act(async () => {
                if (j.status === "paused" || j.status === "pausing") await api("/api/jobs/control", { job_id: j.id, action: "resume" });
                else await api("/api/jobs/resume", { job_id: j.id });
                await reload(); onChange?.();
              }, "Resumed")}>▶ Resume</button>
            ) : (
              <>
                {j.status === "running" && <button className="sm" onClick={() => control(j.id, "pause")}>⏸</button>}
                <ConfirmButton className="sm danger" confirm="Tap to stop" onConfirm={() => control(j.id, "stop")}>■ Stop</ConfirmButton>
              </>
            )}
          </div>
        );
      })}
    </div>
  );
}
