"use client";
import Link from "next/link";
import { api, useApi } from "@/lib/api";
import { money } from "@/lib/fmt";
import { Busy, Card, Empty, Pill, useAct } from "./ui";
import ScheduledList from "./ScheduledList";

const dayWord = (d: number | null | undefined) => d == null ? "" : d === 0 ? "today" : d === 1 ? "tomorrow" : `in ${d} days`;
const WAIT = ["budget_paused", "interrupted", "paused"];

function Cover({ sid, w = 44 }: { sid?: string | null; w?: number }) {
  return sid ? (
    <img src={`/api/watchlist/cover/${encodeURIComponent(sid)}`} alt="" loading="lazy"
      style={{ width: w, aspectRatio: "3/4", objectFit: "cover", borderRadius: 6, background: "var(--panel2)", flex: "none" }}
      onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
  ) : <div style={{ width: w, aspectRatio: "3/4", borderRadius: 6, background: "var(--panel2)", flex: "none" }} />;
}

/** Library → Scheduled for processing (owner, 2026-10-06: "why is there an
 *  Upcoming tab — shift it into Scheduled for processing, with image cards").
 *  Everything autopilot does, top to bottom: its state and limits, what is
 *  being made now, your scheduled list, then what it takes after that. */
export default function ScheduleBoard() {
  const ap = useApi<any>("/api/autopilot", 15000);
  const jobs = useApi<any>("/api/jobsbar?failed=1", 5000);
  const rows = useApi<any>("/api/chapters", 15000);
  const act = useAct();
  const d = ap.data;
  const fc: any[] = d?.forecast || [];
  const after = fc.filter((x) => x.from === "round_robin");
  const byJob: Record<string, any> = {};
  for (const r of rows.data?.chapters || []) if (r.job) byJob[r.job] = r;
  const making = (jobs.data?.jobs || []).filter((j: any) => ["ingest", "autopilot"].includes(j.kind));
  const renders = (jobs.data?.jobs || []).filter((j: any) => j.kind === "finalize" || j.status === "in_queue");
  const pin = (x: any, top: boolean) => act(async () => {
    await api("/api/autopilot/priority", { action: "add", series_id: x.series_id, chapters: [String(x.chapter)] });
    if (top) await api("/api/autopilot/priority", { action: "top", series_id: x.series_id, chapters: [String(x.chapter)] });
    ap.reload();
  }, top ? `${x.title} ch.${x.chapter} is next` : `${x.title} ch.${x.chapter} scheduled`);

  return (
    <div className="grid">
      <Card>
        {!d ? <Empty>Loading…</Empty> : (
          <div className="spread">
            <div className="row">
              <Pill tone={d.enabled ? "ok" : "muted"}>{d.enabled ? "● Autopilot on" : "Autopilot off"}</Pill>
              <span className="small">{d.today} of {d.per_day} chapters today · autopilot {money(d.ap_spent_usd)} of {money(d.budget_usd)} · site {money(d.spent_usd)} of {money(d.cap_usd)} · about {money(d.estimate_usd)} a chapter</span>
            </div>
            <Link className="btn sm" href="/settings">Limits and budget</Link>
          </div>
        )}
        {d?.waiting && <div className="banner warn" style={{ marginTop: 8 }}>Waiting: {d.waiting}</div>}
        {d && !d.enabled && <div className="banner info" style={{ marginTop: 8 }}>Autopilot is off, so nothing below starts. Turn it on in the menu’s footer or in Settings.</div>}
      </Card>

      <Card title={`Being made now (${making.length})`} pad={false}>
        <div className="list">
          {making.length === 0 ? <Empty>Nothing is being made right now.</Empty> : making.map((j: any) => {
            const r = byJob[j.id];
            const waiting = WAIT.includes(j.status);
            return (
              <div className="it" key={j.id} style={{ alignItems: "flex-start" }}>
                <div className="row" style={{ flex: 1, minWidth: 0, flexWrap: "nowrap", alignItems: "flex-start" }}>
                  <Cover sid={r?.series_id} />
                  <div style={{ display: "grid", gap: 4, minWidth: 0, flex: 1 }}>
                    <b>{j.name}</b>
                    <span className="small muted">{waiting ? <Pill tone="warn">{j.status === "budget_paused" ? "paused by a limit" : String(j.status).replace("_", " ")}</Pill> : <Pill tone="info">{j.stage || j.status}</Pill>}
                      {" "}{String(j.msg || "").slice(0, 160)}</span>
                    {!waiting && j.pct != null && <div className="bar"><i style={{ width: `${j.pct || 3}%` }} /></div>}
                  </div>
                </div>
                {r && <Link className="btn sm ghost" href={`/chapter/${encodeURIComponent(r.id)}`}>Open</Link>}
              </div>
            );
          })}
        </div>
        {making.some((j: any) => j.status === "budget_paused") && (
          <div className="small faint" style={{ padding: "8px 14px" }}>A chapter paused by a daily limit resumes by itself as soon as there is room (a raised limit) or at midnight ET; autopilot waits for it before starting another.</div>
        )}
      </Card>

      <ScheduledList ap={d} reload={ap.reload} />

      <Card pad={false} title={`Then autopilot takes these (${after.length} shown)`}>
        <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)" }} className="small muted">
          After your list, autopilot takes turns: the series it has made the fewest chapters for goes next. 📌 schedules a chapter at the end of your list; ⤒ makes it next.
        </div>
        <div className="list">
          {after.length === 0 ? <Empty>Nothing else is waiting — every series is caught up, paused or blocked.</Empty> : after.map((x, i) => (
            <div className="it" key={`${x.series_id}|${x.chapter}`}>
              <div className="row" style={{ minWidth: 0, flex: 1, flexWrap: "nowrap" }}>
                <span className="num faint" style={{ minWidth: 26 }}>#{(d?.priority?.length || 0) + i + 1}</span>
                <Cover sid={x.series_id} w={36} />
                <div style={{ display: "grid", minWidth: 0 }}>
                  <b>{x.title} ch.{x.chapter}</b>
                  <span className="small muted">{dayWord(x.day)}</span>
                </div>
              </div>
              <div className="row">
                <Busy className="sm ghost" title="schedule it (end of your list)" onClick={() => pin(x, false)}>📌</Busy>
                <Busy className="sm ghost" title="make it next" onClick={() => pin(x, true)}>⤒</Busy>
              </div>
            </div>
          ))}
        </div>
        <div style={{ padding: "8px 14px" }} className="small faint">Days come from the chapters-a-day limit ({d?.per_day ?? "—"}); a daily limit running out can push them later.</div>
      </Card>

      {renders.length > 0 && (
        <Card pad={false} title={`Rendering (${renders.length})`} right={<Link className="small" href="/activity?tab=renders">Renders →</Link>}>
          <div className="list">{renders.map((j: any) => (
            <div className="it" key={j.id}><span><b>{j.name}</b> <span className="small muted">{j.status === "in_queue" ? `waiting · ${j.msg}` : `${j.stage || ""} ${j.msg || ""}`}</span></span>
              {j.project && <Link className="btn sm ghost" href={`/chapter/${encodeURIComponent(j.project)}`}>Open</Link>}</div>
          ))}</div>
        </Card>
      )}
    </div>
  );
}
