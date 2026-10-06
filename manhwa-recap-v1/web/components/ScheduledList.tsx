"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { Busy, Card, ConfirmButton, Empty, Pill, useAct } from "./ui";

const TONE: Record<string, string> = { ready: "ok", queued: "muted", running: "info", waiting: "warn", blocked: "bad", no_source: "bad" };
const WORD: Record<string, string> = { ready: "ready", queued: "in line", running: "being made", waiting: "waiting", blocked: "needs you", no_source: "no source" };
const dayWord = (d: number | null | undefined) => d == null ? "" : d === 0 ? "today" : d === 1 ? "tomorrow" : `in ${d} days`;

/** Scheduled for processing (owner, 2026-10-05): the chapters you picked for
 *  autopilot, grouped by series as cover cards (one set per series), with
 *  Scrapper's Mix & Shuffle and Undo. "In order" shows the exact sequence and
 *  lets you drag single chapters. */
export default function ScheduledList({ ap, reload }: { ap: any; reload: () => void }) {
  const act = useAct();
  const [view, setView] = useState<"groups" | "order">("groups");
  const [mixOpen, setMixOpen] = useState(false);
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const list: any[] = ap?.priority || [];
  const fc: any[] = ap?.forecast || [];
  const canUndo = ap?.settings?.priority_prev != null;
  const dayOf = (r: any) => fc.find((x) => x.series_id === r.series_id && String(x.chapter) === String(r.chapter))?.day;
  const edit = (body: any, ok?: string) => act(async () => { await api("/api/autopilot/priority", body); reload(); }, ok);
  const setOrder = (rows: any[]) => edit({ action: "set", order: rows.map((r) => ({ series_id: r.series_id, chapter: r.chapter })) });

  // groups in the order each series first appears
  const groups: { sid: string; title: string; items: { r: any; pos: number }[] }[] = [];
  list.forEach((r, i) => {
    let g = groups.find((x) => x.sid === r.series_id);
    if (!g) { g = { sid: r.series_id, title: r.title, items: [] }; groups.push(g); }
    g.items.push({ r, pos: i + 1 });
  });
  const moveGroup = (from: number, to: number) => {
    if (to < 0 || to >= groups.length || from === to) return;
    const gs = [...groups];
    const [g] = gs.splice(from, 1);
    gs.splice(to, 0, g);
    setOrder(gs.flatMap((x) => x.items.map((it) => it.r)));
  };
  const moveRow = (from: number, to: number) => {
    if (to < 0 || to >= list.length || from === to) return;
    const rows = [...list];
    const [x] = rows.splice(from, 1);
    rows.splice(to, 0, x);
    setOrder(rows);
  };
  const mix = (mode: string, label: string) => { setMixOpen(false); edit({ action: "mix", mode }, `Mixed: ${label}`); };

  return (
    <Card pad={false} title={`Scheduled for processing (${list.length} chapters · ${groups.length} series)`}
      right={<div className="row">
        <button className={`sm ${view === "groups" ? "primary" : "ghost"}`} onClick={() => setView("groups")}>By series</button>
        <button className={`sm ${view === "order" ? "primary" : "ghost"}`} onClick={() => setView("order")}>In order</button>
        <span style={{ position: "relative" }}>
          <button className="sm" disabled={list.length < 2} onClick={() => setMixOpen(!mixOpen)} aria-expanded={mixOpen}>🔀 Mix ▾</button>
          {mixOpen && (
            <div className="card" style={{ position: "absolute", right: 0, top: "110%", zIndex: 30, width: 270, display: "grid", gap: 6, padding: 10 }}>
              <button className="sm primary" onClick={() => mix("round_robin", "one of each series in turn")}>🔄 Round-robin (recommended)<br /><span className="small">one chapter from each series in turn</span></button>
              <button className="sm" onClick={() => mix("by_series", "each series kept together")}>📚 By series<br /><span className="small">finish one series, then the next</span></button>
              <button className="sm" onClick={() => mix("random", "random")}>🎲 Random<br /><span className="small">shuffled; each series stays in story order</span></button>
            </div>
          )}
        </span>
        {canUndo && <Busy className="sm ghost" onClick={() => edit({ action: "undo" }, "Undone")}>↶ Undo</Busy>}
        {list.length > 0 && <ConfirmButton className="sm ghost" confirm="Clear the whole list?" onConfirm={() => edit({ action: "clear" }, "Cleared (Undo brings it back)")}>Clear</ConfirmButton>}
      </div>}>
      <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)" }} className="small muted">
        Autopilot makes these first, by their numbers (#1 first), then goes back to taking turns. A series' chapters always stay in story order. Add chapters from Library → a series → Chapters.
        {ap && !ap.enabled && <b> Autopilot is off, so nothing starts.</b>}
        {ap?.waiting && <> Waiting: {ap.waiting}.</>}
      </div>
      {list.length === 0 ? <Empty>Nothing scheduled. Open a series → Chapters, tick chapters and press “Schedule for processing”.</Empty> :
        view === "groups" ? (
          <div className="list">
            {groups.map((g, gi) => (
              <div className="it" key={g.sid} draggable onDragStart={() => setDrag(gi)} onDragOver={(e) => { e.preventDefault(); setOver(gi); }}
                onDragEnd={() => { setDrag(null); setOver(null); }} onDrop={(e) => { e.preventDefault(); if (drag != null) moveGroup(drag, gi); setDrag(null); setOver(null); }}
                style={{ alignItems: "stretch", cursor: "grab", outline: over === gi && drag !== gi ? "2px dashed var(--accent)" : undefined, opacity: drag === gi ? 0.5 : 1 }}>
                <div className="row" style={{ flex: 1, minWidth: 0, alignItems: "flex-start", flexWrap: "nowrap" }}>
                  <img src={`/api/watchlist/cover/${encodeURIComponent(g.sid)}`} alt="" loading="lazy"
                    style={{ width: 64, aspectRatio: "3/4", objectFit: "cover", borderRadius: 6, background: "var(--panel2)", flex: "none" }}
                    onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
                  <div style={{ display: "grid", gap: 6, minWidth: 0 }}>
                    <b>{g.title} <span className="small muted">· {g.items.length} chapter{g.items.length === 1 ? "" : "s"}</span></b>
                    <div className="row" style={{ gap: 6 }}>
                      {g.items.map(({ r, pos }) => (
                        <span key={r.chapter} className="pill t-muted" title={r.reason || ""} style={{ display: "inline-flex", gap: 6, alignItems: "center" }}>
                          <span className="faint">#{pos}</span> ch.{r.chapter}
                          {r.state !== "ready" && <Pill tone={TONE[r.state] || "muted"}>{WORD[r.state] || r.state}</Pill>}
                          {r.state === "ready" && dayWord(dayOf(r)) && <span className="faint">{dayWord(dayOf(r))}</span>}
                          <button className="sm ghost" style={{ padding: "0 4px" }} aria-label={`Remove ch.${r.chapter}`}
                            onClick={() => edit({ action: "remove", series_id: r.series_id, chapters: [r.chapter] }, `Removed ch.${r.chapter}`)}>✕</button>
                        </span>
                      ))}
                    </div>
                  </div>
                </div>
                <div className="row" style={{ alignSelf: "center" }}>
                  <button className="sm ghost" aria-label="Move series up" disabled={gi === 0} onClick={() => moveGroup(gi, gi - 1)}>↑</button>
                  <button className="sm ghost" aria-label="Move series down" disabled={gi === groups.length - 1} onClick={() => moveGroup(gi, gi + 1)}>↓</button>
                  <Busy className="sm ghost" title="make this whole series next" onClick={() => edit({ action: "group_top", series_id: g.sid }, `${g.title} is next`)}>⤒</Busy>
                  <ConfirmButton className="sm ghost" confirm={`Remove all ${g.items.length}?`} onConfirm={() => edit({ action: "group_remove", series_id: g.sid }, `${g.title} removed`)}>✕</ConfirmButton>
                </div>
              </div>
            ))}
            <div className="it small faint">Moving or dragging a series puts its chapters together; use Mix → Round-robin to interleave them again.</div>
          </div>
        ) : (
          <div className="list">
            {list.map((r, i) => (
              <div className="it" key={`${r.series_id}|${r.chapter}`} draggable onDragStart={() => setDrag(i)} onDragOver={(e) => { e.preventDefault(); setOver(i); }}
                onDragEnd={() => { setDrag(null); setOver(null); }} onDrop={(e) => { e.preventDefault(); if (drag != null) moveRow(drag, i); setDrag(null); setOver(null); }}
                style={{ cursor: "grab", outline: over === i && drag !== i ? "2px dashed var(--accent)" : undefined, opacity: drag === i ? 0.5 : 1 }}>
                <div className="row" style={{ minWidth: 0, flex: 1 }}>
                  <span className="num faint" style={{ minWidth: 26 }}>#{i + 1}</span>
                  <img src={`/api/watchlist/cover/${encodeURIComponent(r.series_id)}`} alt="" loading="lazy"
                    style={{ width: 28, aspectRatio: "3/4", objectFit: "cover", borderRadius: 4, background: "var(--panel2)" }}
                    onError={(e) => { (e.target as HTMLImageElement).style.visibility = "hidden"; }} />
                  <b>{r.title} ch.{r.chapter}</b>
                  <Pill tone={TONE[r.state] || "muted"}>{WORD[r.state] || r.state}</Pill>
                  {r.state === "ready" && dayWord(dayOf(r)) && <span className="small muted">{dayWord(dayOf(r))}</span>}
                  {r.reason && <span className="small muted">{r.reason}</span>}
                </div>
                <div className="row">
                  <button className="sm ghost" aria-label="Move up" disabled={i === 0} onClick={() => moveRow(i, i - 1)}>↑</button>
                  <button className="sm ghost" aria-label="Move down" disabled={i === list.length - 1} onClick={() => moveRow(i, i + 1)}>↓</button>
                  {i > 0 && <Busy className="sm ghost" title="make it next" onClick={() => edit({ action: "top", series_id: r.series_id, chapters: [r.chapter] })}>⤒</Busy>}
                  <Busy className="sm ghost" aria-label="Remove" onClick={() => edit({ action: "remove", series_id: r.series_id, chapters: [r.chapter] }, "Removed")}>✕</Busy>
                </div>
              </div>
            ))}
          </div>
        )}
    </Card>
  );
}
