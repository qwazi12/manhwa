"use client";
import { useEffect, useRef, useState } from "react";
import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { ago, money, when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, PageHead, Pill, Tabs, useAct } from "@/components/ui";

type T = "live" | "renders" | "jobs" | "spend" | "calls" | "changes";
const KINDS = ["", "autopilot", "ingest", "render", "publish", "research", "tracker", "settings", "scheduler"];

export default function Activity() {
  const [tab, setTab] = useState<T>("live");
  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as T; if (t) setTab(t); } catch {}
  }, []);
  return (
    <>
      <PageHead title="Activity" sub="What the studio is doing, what it did, and what it cost." />
      <Tabs<T> value={tab} onChange={setTab} tabs={[["live", "Live"], ["renders", "Renders"], ["jobs", "Jobs"], ["spend", "Spend"], ["calls", "Calls"], ["changes", "What changed"]]} />
      {tab === "live" && <Live />}
      {tab === "renders" && <Renders />}
      {tab === "jobs" && <Jobs />}
      {tab === "spend" && <Spend />}
      {tab === "calls" && <Calls />}
      {tab === "changes" && <Changes />}
    </>
  );
}

function Live() {
  const [events, setEvents] = useState<any[]>([]);
  const [kind, setKind] = useState("");
  const [q, setQ] = useState("");
  const last = useRef(0);
  useEffect(() => {
    let stop = false;
    async function poll() {
      try {
        const d = await api(`/api/events?after=${last.current}&limit=300`);
        if (d.events?.length) {
          last.current = d.events[d.events.length - 1].id;
          setEvents((old) => [...old, ...d.events].slice(-1000));
        }
      } catch {}
      if (!stop) setTimeout(poll, document.hidden ? 15000 : 4000);
    }
    poll();
    return () => { stop = true; };
  }, []);
  const shown = events.filter((e) => (!kind || e.kind === kind) && (!q || e.msg.toLowerCase().includes(q.toLowerCase()))).slice().reverse();
  return (
    <Card pad={false} title={<Chips value={kind} onChange={setKind} items={KINDS.map((k) => [k, k || "All"] as [string, string])} />}
      right={<input placeholder="Search" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 220 }} aria-label="Search activity" />}>
      <div className="list" style={{ maxHeight: "70vh", overflowY: "auto" }}>
        {shown.length === 0 ? <Empty>Nothing yet.</Empty> : shown.map((e) => (
          <div className="it" key={e.id} style={{ alignItems: "baseline" }}>
            <span className="row" style={{ flex: 1, minWidth: 0, flexWrap: "nowrap", alignItems: "baseline" }}>
              <span className="mono small faint" style={{ whiteSpace: "nowrap" }}>{new Date(e.ts * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              <Pill tone={e.level === "error" ? "bad" : e.level === "warn" ? "warn" : e.level === "ok" ? "ok" : "muted"}>{e.kind}</Pill>
              <span style={{ minWidth: 0 }}>{e.msg}</span>
            </span>
          </div>
        ))}
      </div>
    </Card>
  );
}

function Jobs() {
  const { data, reload } = useApi<any>("/api/logs/jobs", 8000);
  const act = useAct();
  const groups: [string, string][] = [["running", "Running"], ["waiting", "Waiting"], ["today", "Today"], ["earlier", "Earlier"]];
  return (
    <div className="grid">
      {groups.map(([k, l]) => (
        <Card key={k} title={`${l} (${(data?.[k] || []).length})`} pad={false}>
          <div className="list">
            {(data?.[k] || []).length === 0 ? <Empty>None.</Empty> : data[k].slice(0, 40).map((j: any) => (
              <div className="it" key={j.id}>
                <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
                  <b>{j.name}{j.deleted ? " (deleted)" : ""}</b>
                  <span className="small muted">{j.kind} · {j.status}{j.stage ? ` · ${j.stage}` : ""}{j.error ? ` · ${String(j.error).slice(0, 140)}` : ""} · {ago(j.ts)}{j.cost ? ` · ${money(j.cost)}` : ""}</span>
                </div>
                <div className="row">
                  {k === "running" && <ConfirmButton className="sm danger" confirm="Tap to stop" onConfirm={() => act(async () => { await api("/api/jobs/control", { job_id: j.id, action: "stop" }); reload(); }, "Stopping")}>■ Stop</ConfirmButton>}
                  {k === "waiting" && <Busy className="sm" onClick={() => act(async () => { await api("/api/jobs/resume", { job_id: j.id }); reload(); }, "Resumed")}>▶ Resume</Busy>}
                  {(k === "today" || k === "earlier") && <ConfirmButton className="sm ghost" confirm="Delete the record?" onConfirm={() => act(async () => { await api("/api/jobs/delete", { job_ids: [j.id] }); reload(); }, "Record deleted")}>✕</ConfirmButton>}
                </div>
              </div>
            ))}
          </div>
        </Card>
      ))}
    </div>
  );
}

function Spend() {
  const { data: d } = useApi<any>("/api/spend", 30000);
  if (!d) return <Empty>Loading…</Empty>;
  const max = Math.max(0.01, ...d.month.days.map((x: any) => x[1]));
  return (
    <div className="grid">
      <div className="tiles">
        <div className="tile"><b>{money(d.today.spent)}</b><span>today, of {money(d.today.cap)} (hard stop)</span></div>
        <div className="tile"><b>{money(d.autopilot.spent)}</b><span>autopilot today, of {money(d.autopilot.budget)}</span></div>
        <div className="tile"><b>{money(d.month.total)}</b><span>{d.month.label} so far</span></div>
      </div>
      <Card title={`By day · ${d.month.label}`}>
        <div style={{ display: "flex", gap: 4, alignItems: "flex-end", height: 140, overflowX: "auto" }}>
          {d.month.days.map(([day, v]: any) => (
            <div key={day} title={`${day}: ${money(v)}`} style={{ flex: "1 0 14px", display: "grid", gap: 4, justifyItems: "center" }}>
              <div style={{ width: "100%", height: Math.max(2, (v / max) * 110), background: "var(--accent)", borderRadius: 3 }} />
              <span className="faint" style={{ fontSize: 10 }}>{day.slice(8)}</span>
            </div>
          ))}
        </div>
        <div className="row small muted">{Object.entries(d.month.by_provider || {}).map(([k, v]: any) => <span key={k}>{k}: {money(v)}</span>)}</div>
      </Card>
      <Card title="Chapters made recently" pad={false}>
        <div className="list">{d.chapters.map((c: any, i: number) => (
          <div className="it" key={i}><span>{c.name}</span><span className="num">{money(c.cost)}</span></div>))}</div>
      </Card>
      <p className="small muted">{d.note} Prices read {d.rates?.gemini_prices_read}.</p>
    </div>
  );
}

function Changes() {
  const { data } = useApi<any>("/api/worklog");
  return (
    <Card title={`What changed · deployed ${data?.deployed_commit || "…"}`} pad={false}>
      <div className="list">{(data?.entries || []).map((e: any) => (
        <details key={e.n} className="it" style={{ display: "block" }}>
          <summary><b>{e.title}</b> <span className="small faint">{e.date}</span></summary>
          <div className="small muted" style={{ whiteSpace: "pre-wrap", marginTop: 6 }}>{e.body}</div>
          {e.evidence_files && Object.keys(e.evidence_files).length > 0 && (
            <div className="row small" style={{ marginTop: 6 }}>Evidence:{Object.entries(e.evidence_files).flatMap(([slug, files]: any) => files.map((f: string) => (
              <a key={slug + f} href={`/api/evidence/${encodeURIComponent(slug)}/${encodeURIComponent(f)}`} target="_blank" rel="noreferrer">{f}</a>)))}</div>
          )}
        </details>))}</div>
    </Card>
  );
}

function Calls() {
  const { data } = useApi<any>("/api/logs/usage?n=150", 20000);
  const sm = data?.summary || {};
  return (
    <Card title={`Every paid call · today ${money(sm.est_cost_usd)} · ${sm.gemini_calls ?? "—"} Gemini, ${sm.claude_calls ?? 0} Claude, ${(sm.tts_chars ?? 0).toLocaleString()} voice characters`} pad={false}>
      <div className="list" style={{ maxHeight: "70vh", overflowY: "auto" }}>
        {(data?.calls || []).map((c: any, i: number) => (
          <div className="it small" key={i}>
            <span className="row" style={{ flex: 1, minWidth: 0 }}>
              <span className="mono faint">{new Date(c.ts).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
              <b>{c.model || c.kind}</b>
              <span className="muted">{c.prompt_tokens != null ? `${c.prompt_tokens} in · ${c.output_tokens} out${c.thought_tokens ? ` (${c.thought_tokens} thinking)` : ""}` : `${c.units} ${c.unit}`}{c.service_tier ? ` · ${c.service_tier}` : ""}{c.metered === false ? " · estimated" : ""}</span>
              <span className="faint">job {String(c.job_id).slice(0, 12)}</span>
            </span>
            <span className="num">{money(c.est_cost_usd)}</span>
          </div>
        ))}
        {!data?.calls?.length && <Empty>No calls logged.</Empty>}
      </div>
    </Card>
  );
}

const STAGE: Record<string, string> = { queued: "starting", revoice: "re-recording the voice", render: "rendering clips", export: "exporting" };

/** The render queue: one row per chapter, in order, with a progress bar while it renders. */
function Renders() {
  const { data, reload } = useApi<any>("/api/render-queue", 3000);
  const act = useAct();
  const items: any[] = data?.items || [];
  const live = items.filter((i) => i.status === "rendering" || i.status === "waiting");
  const done = items.filter((i) => i.status === "done" || i.status === "error").sort((a, b) => (b.ended || 0) - (a.ended || 0));
  const row = (i: any, n?: number) => {
    const p = i.progress;
    const pct = p?.total ? Math.round(100 * (p.done || 0) / p.total) : p?.stage === "export" ? 95 : 4;
    return (
      <div className="it" key={i.id} style={{ display: "grid", gap: 6 }}>
        <div className="spread">
          <span className="row" style={{ minWidth: 0 }}>
            <Pill tone={i.status === "done" ? "ok" : i.status === "error" ? "bad" : i.status === "rendering" ? "warn" : "muted"}>
              {i.status === "waiting" ? (n === 1 ? "next" : `#${n}`) : i.status}</Pill>
            <Link href={`/chapter/${encodeURIComponent(i.project)}${i.status === "done" ? "?tab=video" : ""}`}><b>{i.name}</b></Link>
            {!i.keep_voice && <span className="small faint">re-recording voice (paid)</span>}
          </span>
          <span className="row small muted">
            {i.status === "rendering" && p && <span>{STAGE[p.stage] || p.stage}{p.total && p.stage !== "export" ? ` · ${p.done || 0} of ${p.total}` : ""}</span>}
            {i.ended && <span>{ago(i.ended)}</span>}
            {i.status === "waiting" && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/render-queue/remove", { id: i.id }); reload(); }, "Taken out")}>✕</Busy>}
            {i.status === "rendering" && i.job && <ConfirmButton className="sm danger" confirm="Stop this render?" onConfirm={() => act(async () => { await api("/api/jobs/control", { job_id: i.job, action: "stop" }); reload(); }, "Stopping")}>■ Stop</ConfirmButton>}
            {i.status === "done" && <Link className="btn sm" href={`/chapter/${encodeURIComponent(i.project)}?tab=video`}>▶ Watch</Link>}
          </span>
        </div>
        {i.status === "rendering" && <div className="bar"><i style={{ width: `${pct}%` }} /></div>}
        {i.status === "error" && i.error && <span className="small" style={{ color: "var(--red)" }}>{i.error}</span>}
      </div>
    );
  };
  let w = 0;
  return (
    <div className="grid">
      <Card title={`Rendering and waiting (${live.length})`} pad={false}>
        <div className="list">{live.length === 0
          ? <Empty>Nothing queued. Tick chapters in Library → All chapters and press “Render selected”, or press Render on a chapter.</Empty>
          : live.map((i) => row(i, i.status === "waiting" ? ++w : undefined))}</div>
      </Card>
      <Card title={`Finished (${done.length})`} pad={false}
        right={done.length > 0 && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/render-queue/clear", {}); reload(); }, "Cleared")}>Clear finished</Busy>}>
        <div className="list">{done.length === 0 ? <Empty>None yet.</Empty> : done.map((i) => row(i))}</div>
      </Card>
      <p className="small muted">Chapters render one at a time, in order, each in its own voice — free. While one renders, other boards are view-only.</p>
    </div>
  );
}
