"use client";
import { useEffect, useState } from "react";
import { api } from "@/lib/api";
import { when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, Pill, useAct } from "@/components/ui";
import MediaQaCard from "./MediaQaCard";

type Mode = "rules" | "text" | "full";
const SEV: Record<string, string> = { high: "bad", medium: "warn", low: "muted" };

/** Every quality signal for the chapter in one place: timing and pacing,
 *  the story check, and what the script editor changed. */
export default function Issues({ id, onFixed }: { id: string; onFixed?: () => void }) {
  const act = useAct();
  const [timing, setTiming] = useState<any>(null);
  const [check, setCheck] = useState<any>(null);
  const [crit, setCrit] = useState<any>(null);
  const [source, setSource] = useState<any>(null);
  const [coverage, setCoverage] = useState<any>(null);
  const [mode, setMode] = useState<Mode>("rules");
  const [err, setErr] = useState<string | null>(null);
  async function load() {
    try {
      await api("/api/activate", { id });
      const [t, v, c, s, cov] = await Promise.all([api("/api/validate"), api("/api/validation"), api(`/api/critique?project=${encodeURIComponent(id)}`), api(`/api/source-review?project=${encodeURIComponent(id)}`), api(`/api/source-coverage?project=${encodeURIComponent(id)}`).catch(() => ({ status: "incomplete", error: "Source linkage check unavailable." }))]);
      setTiming(t); setCheck(v.report); setCrit(c); setSource(s); setCoverage(cov); setErr(null);
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
      <MediaQaCard id={id} />
      <Card title="Source dialogue">
        {!source || source.missing ? <p className="muted">Dialogue transfer was not recorded for this version.</p> : <>
          <p className="small muted">{(source.orphans || []).length} bubble panel{(source.orphans || []).length === 1 ? " needs" : "s need"} placement review. This transfer check does not prove complete narrative coverage.</p>
          {(source.orphans || []).map((o: any) => <div className="sugg" key={o.panel_id}>
            <Pill tone="warn">Unplaced source text · {o.panel_id}</Pill><p>{o.text}</p><p className="small muted">{o.reason} Review the neighboring artwork before choosing a speaker or image.</p>
          </div>)}
        </>}
      </Card>
      <Card title="Source evidence links">
        <p className="small muted">{coverage?.status === "evidence_linked" ? "Eligible source evidence has script references." : "Some source evidence needs review."} References do not prove that narration covers the story. Semantic coverage has not been assessed.</p>
        {coverage?.error && <p role="alert" className="banner warn">{coverage.error}</p>}
        <p className="small muted">{coverage?.status === "incomplete" ? "No complete source linkage scan is available." : `Source lines: ${coverage?.lines?.length || 0} · artwork descriptions: ${coverage?.events?.length || 0} · review candidates: ${coverage?.findings?.length || 0}`}</p>
        {!!coverage?.findings?.length && <details><summary>Review source candidates</summary>
          {coverage.findings.map((f: any, i: number) => <div className="sugg" key={`${f.source_line_id || f.panel_id || f.kind}-${i}`}>
            <Pill tone="warn">{f.panel_id || "Script versions"}</Pill><p>{f.text}</p><p className="small muted">{f.reason}</p>
          </div>)}
        </details>}
      </Card>
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
          {findings.length === 0 ? <Empty>{check?.status === "running" ? "Checking…" : check?.status === "ok" ? "No findings in the checks that ran." : "No completed story check yet."}</Empty> : findings.map((f) => (
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
            <p className="small muted">Second-pass status: {crit.status} · {(crit.issues || []).length} recorded issues · {(crit.revised || []).length} parts rewritten (at most 3).</p>
            <p className={crit.outcome === "clean" ? "small muted" : "banner warn"}>Final review outcome: {String(crit.outcome || "unverified legacy record").replace(/_/g, " ")}. {crit.outcome === "clean" || crit.outcome === "needs_review" ? `${(crit.unresolved || []).length} known issues remain unresolved or rewritten without verification.` : "The final script has not received a complete review."}</p>
            {crit.error && <p role="alert" className="banner warn">{crit.error}</p>}
            {(crit.unresolved || []).map((i: any, k: number) => <div key={`unresolved-${k}`} className="small"><b>Part {i.unit + 1} · {String(i.resolution || "needs review").replace(/_/g, " ")}:</b> {i.problem}</div>)}
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
