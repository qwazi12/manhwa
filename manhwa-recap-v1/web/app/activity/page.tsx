"use client";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, money, when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, PageHead, Pill, Tabs, useAct } from "@/components/ui";

type T = "live" | "jobs" | "spend" | "changes";
const KINDS = ["", "autopilot", "ingest", "render", "publish", "research", "tracker", "settings", "scheduler"];

export default function Activity() {
  const [tab, setTab] = useState<T>("live");
  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as T; if (t) setTab(t); } catch {}
  }, []);
  return (
    <>
      <PageHead title="Activity" sub="What the studio is doing, what it did, and what it cost." />
      <Tabs<T> value={tab} onChange={setTab} tabs={[["live", "Live"], ["jobs", "Jobs"], ["spend", "Spend"], ["changes", "What changed"]]} />
      {tab === "live" && <Live />}
      {tab === "jobs" && <Jobs />}
      {tab === "spend" && <Spend />}
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
        </details>))}</div>
    </Card>
  );
}
