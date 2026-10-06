"use client";
import Link from "next/link";
import { api } from "@/lib/api";
import { Busy, Card, ConfirmButton, Empty, Pill, useAct } from "./ui";

const TONE: Record<string, "ok" | "warn" | "bad" | "muted" | "info"> = {
  ready: "ok", running: "info", waiting: "warn", blocked: "bad", no_source: "bad",
};
const WORD: Record<string, string> = {
  ready: "ready", running: "being made", waiting: "waiting", blocked: "needs you", no_source: "no source",
};

/** Make next (owner, 2026-10-05): the chapters you ticked, in your order.
 *  Autopilot makes these first, one at a time, before its usual round robin.
 *  The chapters-a-day limit and the budget still apply. */
export default function MakeNext({ ap, reload }: { ap: any; reload: () => void }) {
  const act = useAct();
  const list: any[] = ap?.priority || [];
  const edit = (body: any, ok?: string) => act(async () => { await api("/api/autopilot/priority", body); reload(); }, ok);
  const move = (i: number, d: number) => {
    const order = list.map((r) => ({ series_id: r.series_id, chapter: r.chapter }));
    const j = i + d;
    if (j < 0 || j >= order.length) return;
    [order[i], order[j]] = [order[j], order[i]];
    edit({ action: "set", order });
  };
  const status = !ap ? "" : !ap.enabled ? "Autopilot is off. Turn it on (bottom of the menu) and it starts on this list."
    : ap.waiting ? `Autopilot is waiting: ${ap.waiting}.`
    : ap.next ? `Next: ${ap.next.series} ch.${ap.next.chapter}${ap.next.from_next_up ? " (from this list)" : " (round robin)"}.` : "";
  return (
    <Card pad={false} title={`Make next (${list.length})`}
      right={list.length > 0 && <ConfirmButton className="sm ghost" confirm="Clear the list?" onConfirm={() => edit({ action: "clear" }, "List cleared")}>Clear</ConfirmButton>}>
      <div style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", display: "grid", gap: 4 }}>
        <span className="small">Autopilot makes these first, top to bottom, then goes back to taking turns across series. {ap?.today ?? "—"} of {ap?.per_day ?? "—"} chapters made today.</span>
        {status && <span className="small muted">{status}</span>}
      </div>
      <div className="list">
        {list.length === 0 ? (
          <Empty>Nothing picked. Open a series → Chapters, tick the chapters you want, and press “Add to Make next”.</Empty>
        ) : list.map((r, i) => (
          <div className="it" key={`${r.series_id}|${r.chapter}`}>
            <div className="row" style={{ minWidth: 0, flex: 1 }}>
              <span className="num faint" style={{ minWidth: 22 }}>{i + 1}</span>
              <b>{r.title} ch.{r.chapter}</b>
              <Pill tone={TONE[r.state] || "muted"}>{WORD[r.state] || r.state}</Pill>
              {r.reason && <span className="small muted">{r.reason}</span>}
            </div>
            <div className="row">
              <button className="sm ghost" aria-label="Move up" disabled={i === 0} onClick={() => move(i, -1)}>↑</button>
              <button className="sm ghost" aria-label="Move down" disabled={i === list.length - 1} onClick={() => move(i, 1)}>↓</button>
              {i > 0 && <Busy className="sm ghost" title="move to the top" onClick={() => edit({ action: "top", series_id: r.series_id, chapters: [r.chapter] })}>⤒</Busy>}
              <Busy className="sm ghost" aria-label="Remove" onClick={() => edit({ action: "remove", series_id: r.series_id, chapters: [r.chapter] }, "Removed")}>✕</Busy>
            </div>
          </div>
        ))}
      </div>
      <div style={{ padding: "8px 14px" }} className="small faint">
        A chapter leaves the list once it’s made; find it under <Link href="/library?view=chapters">All chapters</Link>. One that fails is skipped so the rest keep going.
      </div>
    </Card>
  );
}
