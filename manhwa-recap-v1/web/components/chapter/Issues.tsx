"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, Pill, useAct } from "@/components/ui";

type Mode = "rules" | "text" | "full";
const SEV: Record<string, string> = { high: "bad", medium: "warn", low: "muted" };

/** Every quality signal for the chapter in one place: timing and pacing,
 *  the story check, and what the script editor changed. */
export default function Issues({ id, onFixed }: { id: string; onFixed?: () => void }) {
  const act = useAct();
  const [timing, setTiming] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [crit, setCrit] = useState<any>(null);
  const [mode, setMode] = useState<Mode>("rules");
  const [err, setErr] = useState<string | null>(null);
  async function load() {
    try {
      await api("/api/activate", { id });
      const [t, v, c] = await Promise.all([api("/api/validate"), api("/api/validation"), api(`/api/critique?project=${encodeURIComponent(id)}`)]);
      setTiming(t); setCheck(v.report); setCrit(c); setErr(null);
    } catch (e: any) { setErr(e.message); }
  }
  useEffect(() => { load(); }, [id]);
  useEffect(() => {
    if (!check || !["queued", "running"].includes(check.status)) return;
    const t = setTimeout(load, 4000);
    return () => clearTimeout(t);
  }, [check]);
  if (err) return <div className="banner bad">{err}</div>;
  if (!timing) return <Empty>Loading…</Empty>;
  const findings: any[] = check?.findings || [];
  return (
    <div className="grid">
      <Card title={`Timing & pacing · ${timing.n_errors} errors, ${timing.n_warnings} warnings`} pad={false}>
        <div className="list">
          {timing.errors.length + timing.warnings.length === 0 ? <Empty>No timing or pacing problems.</Empty> : (
            [...timing.errors.map((x: any) => ({ ...x, sev: "bad" })), ...timing.warnings.map((x: any) => ({ ...x, sev: "warn" }))].map((x: any, i: number) => (
              <div className="it" key={i}>
                <div className="row"><Pill tone={x.sev}>{x.sev === "bad" ? "blocks export" : "warning"}</Pill><span className="small faint">seg {x.seg} · {x.rule}</span></div>
                <span style={{ flex: 1, minWidth: 220 }}>{x.msg}</span>
              </div>
            ))
          )}
          {timing.errors.some((x: any) => /G6|G7/.test(x.rule)) && (
            <div className="it"><span>Some lines play twice or over themselves.</span>
              <Busy className="sm primary" onClick={() => act(async () => { await api("/api/storyboard/repair_slices", { dry_run: false }); await load(); onFixed?.(); }, "Repaired")}>Repair automatically</Busy></div>
          )}
        </div>
      </Card>

      <Card title="Story check" right={<div className="row">
        <Chips<Mode> value={mode} onChange={setMode} items={[["rules", "Rules (free)"], ["text", "+ Claude"], ["full", "+ vision"]]} />
        <ConfirmButton className="sm primary" confirm={mode === "rules" ? "Run now?" : "Run? (costs a few cents)"}
          onConfirm={() => act(async () => { await api("/api/validate", { mode }); await load(); }, "Story check started")}>Run check</ConfirmButton>
      </div>} pad={false}>
        {check && <div className="small muted" style={{ padding: "8px 14px" }}>Last run {when(check.ts ? Date.parse(check.ts) / 1000 : null)} · {check.mode} · {check.status}{check.cost_usd ? ` · $${check.cost_usd.toFixed(3)}` : ""}</div>}
        <div className="list">
          {findings.length === 0 ? <Empty>{check?.status === "running" ? "Checking…" : "No story problems found."}</Empty> : findings.map((f) => (
            <div className="it" key={f.id} style={{ alignItems: "flex-start" }}>
              <div style={{ display: "grid", gap: 4, flex: 1, minWidth: 240 }}>
                <div className="row"><Pill tone={SEV[f.severity] || "muted"}>{f.severity_label || f.severity}</Pill><span className="small faint">row {f.row} · {f.category_label}</span></div>
                <b style={{ fontWeight: 600 }}>{f.issue}</b>
                {f.suggestion && <span className="small muted">{f.suggestion}</span>}
              </div>
              <div className="row">
                {(f.actions || []).filter((a: any) => a.kind === "server").map((a: any) => (
                  <ConfirmButton key={a.id} className="sm" confirm={a.label + "?"} title={a.preview}
                    onConfirm={() => act(async () => { await api("/api/validate/action", { action: a.id, params: a.params || {} }); await load(); onFixed?.(); }, "Done")}>{a.label}</ConfirmButton>
                ))}
              </div>
            </div>
          ))}
        </div>
      </Card>

      <Card title="What the script editor changed">
        {!crit || crit.missing ? <p className="muted">No notes for this chapter (it was scripted before notes were kept).</p> : (
          <>
            <p className="small muted">Second-pass review: {crit.status} · {(crit.issues || []).length} issues found · {(crit.revised || []).length} parts rewritten (at most 3).</p>
            {(crit.issues || []).map((i: any, k: number) => (
              <div key={k} className="small"><b>Part {i.unit + 1} · {String(i.type || "").replace(/_/g, " ")}:</b> {i.problem} {i.fix && <span className="muted">— {i.fix}</span>}</div>
            ))}
            {(crit.revised || []).map((r: any, k: number) => (
              <div key={k} className="sugg">
                <b>Part {r.unit + 1} rewritten</b>
                <div className="small muted" style={{ textDecoration: "line-through" }}>{r.before}</div>
                <div className="small">{r.after}</div>
              </div>
            ))}
          </>
        )}
      </Card>
    </div>
  );
}
