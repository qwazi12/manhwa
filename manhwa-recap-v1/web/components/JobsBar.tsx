"use client";
import { api, useApi } from "@/lib/api";
import { ConfirmButton, useAct } from "./ui";

type Job = { id: string; kind: string; name: string; status: string; stage?: string; pct?: number | null; msg?: string; elapsed?: number };
const WAITING = ["budget_paused", "interrupted", "paused", "pausing", "cancelled"];

/** Everything running, on every page, with pause / resume / stop. */
export default function JobsBar({ onChange }: { onChange?: () => void }) {
  const { data, reload } = useApi<{ jobs: Job[] }>("/api/jobsbar", 5000);
  const act = useAct();
  const jobs = data?.jobs || [];
  if (!jobs.length) return null;
  const control = (id: string, action: string) =>
    act(async () => { await api("/api/jobs/control", { job_id: id, action }); await reload(); onChange?.(); });
  return (
    <div className="jobsbar" aria-live="polite">
      {jobs.map((j) => {
        const waiting = WAITING.includes(j.status);
        return (
          <div className="job" key={j.id}>
            <b>{j.kind === "autopilot" ? "🤖 " : ""}{j.name}</b>
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
