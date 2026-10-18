"use client";
import { api, useApi } from "@/lib/api";
import { when } from "@/lib/fmt";
import { Busy, Card, Pill, useAct } from "@/components/ui";

type Finding = { rule: string; severity: string; explanation: string; start?: number; end?: number; beat?: number; segment?: number; suggestion?: string };
type Result = { name?: string; status: string; fresh?: boolean; enforced: boolean; findings: Finding[]; checked_at?: number; note?: string };

export default function MediaQaCard({ id, name, onSeek }: { id: string; name?: string; onSeek?: (seconds: number) => void }) {
  const enc = encodeURIComponent;
  const result = useApi<Result>(`/api/media-qa?project=${enc(id)}${name ? `&name=${enc(name)}` : ""}`, 5000);
  const act = useAct();
  const q = result.data;
  const exportName = name || q?.name;
  return <Card title="Final video check" right={exportName && <Busy className="sm" disabled={q?.status === "running"}
    onClick={() => act(async () => { await api("/api/media-qa", { project: id, name: exportName }); await result.reload(); }, "Video check started")}>Inspect video</Busy>}>
    {result.error ? <p role="alert" className="banner bad">{result.error}</p> : !q ? <p className="muted">Loading video check…</p> : <>
      <div className="row"><Pill tone={q.status === "passed" ? "ok" : q.status === "failed" ? "bad" : "warn"}>{q.status.replace(/_/g, " ")}</Pill>
        {q.checked_at && <span className="small muted">Checked {when(q.checked_at)}</span>}</div>
      <p className="small muted">{q.enforced ? "Technical failures block posting." : "Diagnostic mode. These findings do not block posting until calibration is approved."} Inspection leaves your video unchanged.</p>
      {q.note && <p className="small muted">{q.note}</p>}
      {q.status === "stale" && <p className="banner warn">The export or its inputs changed. Run the check again and review the current file.</p>}
      {q.status === "incomplete" && <p className="banner warn">This check lacks complete evidence. Older exports need a recorded render manifest; a clean result cannot be inferred.</p>}
      {(q.findings || []).map((f, i) => <div className="sugg" key={`${f.rule}-${f.beat}-${f.start}-${i}`}>
        <div className="row"><Pill tone={f.severity === "critical" ? "bad" : "warn"}>{f.severity}</Pill>
          {typeof f.start === "number" && (onSeek ? <button className="btn sm ghost" onClick={() => onSeek(f.start!)} aria-label={`Seek to ${f.start.toFixed(1)} seconds`}>{f.start.toFixed(1)}s</button> : <span>{f.start.toFixed(1)}s</span>)}
          {f.beat != null && <span className="small muted">beat {f.beat}</span>}{f.segment != null && <span className="small muted">segment {f.segment}</span>}</div>
        <p>{f.explanation}</p>{f.suggestion && <p className="small muted">{f.suggestion}</p>}
      </div>)}
    </>}
  </Card>;
}
