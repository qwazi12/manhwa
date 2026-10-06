"use client";
import Link from "next/link";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { money } from "@/lib/fmt";
import { Busy, Card, Empty, PageHead, Pill, useAct } from "@/components/ui";
import ScheduledList from "@/components/ScheduledList";

const TONE: Record<string, string> = { ready: "ok", running: "info", waiting: "warn", blocked: "bad", no_source: "bad" };
const WORD: Record<string, string> = { ready: "ready", running: "being made", waiting: "waiting", blocked: "needs you", no_source: "no source" };
const dayWord = (d: number | null | undefined) => d == null ? "" : d === 0 ? "today" : d === 1 ? "tomorrow" : `in ${d} days`;

/** Everything autopilot will make, in order, and the controls to change it
 *  (owner, 2026-10-05: "let me see all works that are scheduled and mix them up").
 *  1. being made now · 2. your Make next list (drag to reorder) · 3. what autopilot
 *  takes after it, in round-robin order (pin any of them into your list) · 4. renders. */
export default function Upcoming() {
  const ap = useApi<any>("/api/autopilot", 15000);
  const jobs = useApi<any>("/api/jobsbar?failed=1", 5000);
  const rq = useApi<any>("/api/render-queue", 5000);
  const act = useAct();
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const d = ap.data;
  const list: any[] = d?.priority || [];
  const fc: any[] = d?.forecast || [];
  const dayOf = (r: any) => fc.find((x) => x.series_id === r.series_id && String(x.chapter) === String(r.chapter))?.day;
  const after = fc.filter((x) => x.from === "round_robin");
  const making = (jobs.data?.jobs || []).filter((j: any) => ["ingest", "autopilot"].includes(j.kind));
  const renders = (rq.data?.items || []).filter((i: any) => ["waiting", "rendering"].includes(i.status));
  const edit = (body: any, ok?: string) => act(async () => { await api("/api/autopilot/priority", body); ap.reload(); }, ok);
  const setOrder = (rows: any[]) => edit({ action: "set", order: rows.map((r) => ({ series_id: r.series_id, chapter: r.chapter })) });
  const move = (from: number, to: number) => {
    if (to < 0 || to >= list.length || from === to) return;
    const rows = [...list];
    const [x] = rows.splice(from, 1);
    rows.splice(to, 0, x);
    setOrder(rows);
  };
  const pin = (x: any, top: boolean) => act(async () => {
    await api("/api/autopilot/priority", { action: "add", series_id: x.series_id, chapters: [String(x.chapter)] });
    if (top) await api("/api/autopilot/priority", { action: "top", series_id: x.series_id, chapters: [String(x.chapter)] });
    ap.reload();
  }, top ? `${x.title} ch.${x.chapter} is next` : `${x.title} ch.${x.chapter} added to your list`);

  return (
    <>
      <PageHead title="Upcoming" sub="Everything autopilot will make, in order. Drag your list to reorder it, or pin any later chapter into it." />
      <Card>
        {!d ? <Empty>Loading…</Empty> : (
          <div className="spread">
            <div className="row">
              <Pill tone={d.enabled ? "ok" : "muted"}>{d.enabled ? "● Autopilot on" : "Autopilot off"}</Pill>
              <span className="small">{d.today} of {d.per_day} chapters made today · autopilot spent {money(d.ap_spent_usd)} of {money(d.budget_usd)} · site {money(d.spent_usd)} of {money(d.cap_usd)} · about {money(d.estimate_usd)} a chapter</span>
            </div>
            <Link className="btn sm" href="/settings">Limits and budget</Link>
          </div>
        )}
        {d?.waiting && <div className="banner warn" style={{ marginTop: 8 }}>Waiting: {d.waiting}</div>}
        {d && !d.enabled && <div className="banner info" style={{ marginTop: 8 }}>Autopilot is off, so nothing below starts. Turn it on in the menu’s footer or in Settings.</div>}
      </Card>

      <Card title={`Being made now (${making.length})`} pad={false}>
        <div className="list">
          {making.length === 0 ? <Empty>Nothing is being made right now.</Empty> : making.map((j: any) => (
            <div className="it" key={j.id} style={{ display: "grid", gap: 6 }}>
              <div className="spread"><b>{j.kind === "autopilot" ? "🤖 " : ""}{j.name}</b>
                <span className="small muted">{String(j.status).replace("_", " ")}{j.stage ? ` · ${j.stage}` : ""}{j.msg ? ` · ${String(j.msg).slice(0, 140)}` : ""}</span></div>
              {j.pct != null && !["budget_paused", "interrupted", "paused"].includes(j.status) && <div className="bar"><i style={{ width: `${j.pct || 3}%` }} /></div>}
            </div>
          ))}
        </div>
      </Card>

      <ScheduledList ap={d} reload={ap.reload} />

      <Card pad={false} title={`Then autopilot takes these (${after.length} shown)`}>
        <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)" }} className="small muted">
          After your list, autopilot takes turns: the series it has made the fewest chapters for goes next, ties by your series order. 📌 adds a chapter to the end of your list; ⤒ makes it next.
        </div>
        <div className="list">
          {after.length === 0 ? <Empty>Nothing else is waiting — every series is caught up, paused or blocked.</Empty> : after.map((x, i) => (
            <div className="it" key={`${x.series_id}|${x.chapter}`}>
              <div className="row" style={{ minWidth: 0, flex: 1 }}>
                <span className="num faint" style={{ minWidth: 22 }}>{list.length + i + 1}</span>
                <b>{x.title} ch.{x.chapter}</b>
                <span className="small muted">{dayWord(x.day)}</span>
              </div>
              <div className="row">
                <Busy className="sm ghost" title="add to the end of your list" onClick={() => pin(x, false)}>📌</Busy>
                <Busy className="sm ghost" title="make it next" onClick={() => pin(x, true)}>⤒</Busy>
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding: "8px 14px" }} className="small faint">Days come from the chapters-a-day limit ({d?.per_day ?? "—"}); a day’s budget running out can push them later. Pause a whole series in Library.</div>
      </Card>

      <Card pad={false} title={`Render queue (${renders.length})`} right={<Link className="small" href="/activity?tab=renders">Open Renders →</Link>}>
        <div className="list">
          {renders.length === 0 ? <Empty>No renders waiting. Tick chapters in Library → All chapters and press Render selected.</Empty> : renders.map((i: any, n: number) => (
            <div className="it" key={i.id}><span><b>{i.name}</b> <span className="small muted">{i.status === "rendering" ? "rendering now" : `#${n + 1} in line`}</span></span>
              <Link className="btn sm" href={`/chapter/${encodeURIComponent(i.project)}`}>Open</Link></div>
          ))}
        </div>
      </Card>
    </>
  );
}
