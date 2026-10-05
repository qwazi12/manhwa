"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, when } from "@/lib/fmt";
import PasteBox from "@/components/PasteBox";
import AllChapters from "@/components/AllChapters";
import { Busy, Card, Chips, ConfirmButton, Empty, PageHead, Pill, StatusPill, Tabs, useAct } from "@/components/ui";

const TIERS: [string, string][] = [["greenlight", "Make now"], ["high_upside", "Next up"], ["watchlist", "Watching"]];
const AP_STATE: Record<string, [string, string]> = {
  ready: ["ok", "ready"], running: ["info", "making a chapter"], up_to_date: ["muted", "caught up"],
  paused: ["warn", "paused"], stopped: ["warn", "stopped by you"], cooldown: ["warn", "retrying soon"],
  blocked: ["bad", "needs you"], no_source: ["bad", "no source"],
};
type F = "all" | "greenlight" | "high_upside" | "watchlist" | "needs" | "paused";

export default function Library() {
  const { data, reload, error } = useApi<any>("/api/library", 30000);
  const [f, setF] = useState<F>("all");
  const [q, setQ] = useState("");
  const [view, setView] = useState<"series" | "chapters">("series");
  useEffect(() => { try { if (new URLSearchParams(location.search).get("view") === "chapters") setView("chapters"); } catch {} }, []);
  const series: any[] = data?.series || [];
  const needs = (s: any) => ["to_review", "video_ready", "failed", "waiting"].some((k) => s.counts?.[k]);
  const shown = useMemo(() => series.filter((s) =>
    (f === "all" || s.tier === f || (f === "needs" && needs(s)) || (f === "paused" && s.state === "paused")) &&
    (!q || (s.title || "").toLowerCase().includes(q.toLowerCase()))), [series, f, q]);
  const n = (k: F) => series.filter((s) => k === "all" || s.tier === k || (k === "needs" && needs(s)) || (k === "paused" && s.state === "paused")).length;
  return (
    <>
      <PageHead title="Library" sub="Every series you track, with its chapters. Autopilot makes the next chapter of each series in turn." />
      <PasteBox onDone={reload} />
      <Tabs value={view} onChange={setView} tabs={[["series", "Series"], ["chapters", "All chapters"]]} />
      {view === "chapters" ? <AllChapters /> : <>
      <div className="spread">
        <Chips<F> value={f} onChange={setF} items={[["all", `All (${n("all")})`], ["needs", `Needs you (${n("needs")})`],
          ["greenlight", `Make now (${n("greenlight")})`], ["high_upside", `Next up (${n("high_upside")})`],
          ["watchlist", `Watching (${n("watchlist")})`], ["paused", `Paused (${n("paused")})`]]} />
        <input placeholder="Search series" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} aria-label="Search series" />
      </div>
      {error && <div className="banner bad">{error}</div>}
      {!data ? <Empty>Loading…</Empty> : shown.length === 0 ? <Empty>No series match. Paste a link above to add one.</Empty> : (
        <div className="grid sgrid">{shown.map((s) => <SeriesCard key={s.id} s={s} reload={reload} />)}</div>
      )}
      </>}
    </>
  );
}

function SeriesCard({ s, reload }: { s: any; reload: () => void }) {
  const act = useAct();
  const [open, setOpen] = useState<null | "chapters" | "cast">(null);
  const d = s.demand;
  const paused = s.state === "paused";
  return (
    <Card pad={false}>
      <div style={{ display: "grid", gridTemplateColumns: "84px 1fr", gap: 14, padding: 14 }}>
        {s.cover ? <img className="cover" src={`/api/watchlist/cover/${enc(s.id)}`} alt="" loading="lazy" /> : <div className="cover" />}
        <div style={{ display: "grid", gap: 7, minWidth: 0 }}>
          <div className="spread">
            <h2 style={{ fontSize: 16 }}>{s.title}</h2>
            <select value={s.tier} style={{ width: "auto", minHeight: 30, fontSize: 12.5 }} aria-label="Priority"
              onChange={(e) => act(async () => { await api("/api/watchlist/update", { series_id: s.id, tier: e.target.value }); reload(); }, "Priority changed")}>
              {TIERS.map(([k, l]) => <option key={k} value={k}>{l}</option>)}
            </select>
          </div>
          <div className="row small muted">
            <span>{s.source || "no source"}</span>
            <span>· latest ch.{s.latest || "?"}{s.latest_date ? ` (${s.latest_approx ? "≈ " : ""}${s.latest_date})` : ""}</span>
            {(s.new_since_made || []).length > 0 && <b style={{ color: "var(--text)" }}>· {s.new_since_made.length} new since last made</b>}
          </div>
          <div className="row">
            <Pill tone={(AP_STATE[s.state] || ["muted"])[0]}>Autopilot: {(AP_STATE[s.state] || ["", s.state || "—"])[1]}</Pill>
            <span className="small muted">{s.next ? `next ch.${s.next}${(s.left_in_plan || []).length > 1 ? ` · ${s.left_in_plan.length - 1} more planned` : ""}` : s.reason}</span>
          </div>
          <div className="row small">
            {Object.entries(s.counts || {}).map(([k, v]: any) => (
              <span key={k} className="muted">{v} {k.replace("_", " ")}</span>
            ))}
            {s.bible ? <span className="muted">· cast of {s.bible.characters}{s.bible.disputes ? `, ${s.bible.disputes} name disputes` : ""}</span>
              : <span style={{ color: "var(--yellow)" }}>· no cast list yet</span>}
          </div>
          {d && d.level !== "error" && (
            <div className="row small">
              <span className="muted">Demand on YouTube:</span>
              {d.level === "unknown" ? <span className="muted">no recaps of it found</span> : (
                <>
                  <Pill tone={d.level === "high" ? "ok" : d.level === "medium" ? "warn" : "muted"}>{d.level}</Pill>
                  <span className="muted">{Math.round(d.median_vpd)} views/day typical · {d.recaps} recaps ({d.recent} in 90 days)</span>
                  {d.differs && (
                    <Busy className="sm" onClick={() => act(async () => { await api("/api/watchlist/update", { series_id: s.id, tier: d.suggested_tier }); reload(); }, "Priority changed")}>
                      Set to {TIERS.find((t) => t[0] === d.suggested_tier)?.[1]}
                    </Busy>
                  )}
                </>
              )}
            </div>
          )}
          <div className="row">
            {s.next && (
              <ConfirmButton className="sm primary" confirm={`Make ch.${s.next}? (~$0.50)`}
                onConfirm={() => act(async () => { await api("/api/autopilot/run", { series_id: s.id, chapter: s.next }); reload(); }, `Making ch.${s.next}`)}>
                ▶ Make ch.{s.next} now
              </ConfirmButton>
            )}
            <Busy className="sm" onClick={() => act(async () => { await api("/api/autopilot/series", { series_id: s.id, action: paused ? "resume" : "pause" }); reload(); }, paused ? "Resumed" : "Paused")}>
              {paused ? "▶ Resume" : "⏸ Pause"}
            </Busy>
            {["blocked", "stopped", "cooldown"].includes(s.state) && (
              <Busy className="sm" onClick={() => act(async () => { await api("/api/autopilot/series", { series_id: s.id, action: "retry" }); reload(); }, "Will retry")}>↻ Retry</Busy>
            )}
            <button className={`sm ${open === "chapters" ? "primary" : ""}`} onClick={() => setOpen(open === "chapters" ? null : "chapters")}>Chapters ({(s.chapters || []).length} made)</button>
            <button className={`sm ${open === "cast" ? "primary" : ""}`} onClick={() => setOpen(open === "cast" ? null : "cast")}>Cast</button>
          </div>
        </div>
      </div>
      {open === "chapters" && <ChapterList s={s} reload={reload} />}
      {open === "cast" && <Cast s={s} />}
    </Card>
  );
}

function ChapterList({ s, reload }: { s: any; reload: () => void }) {
  const src = useApi<any>(`/api/series/chapters?series_id=${enc(s.id)}`);
  const act = useAct();
  const made: Record<string, any> = {};
  for (const r of s.chapters || []) made[String(r.chapter)] = r;
  const list: any[] = src.data?.chapters || [];
  return (
    <div style={{ borderTop: "1px solid var(--border)" }}>
      <div className="list" style={{ maxHeight: 420, overflowY: "auto" }}>
        {(s.chapters || []).filter((r: any) => !list.some((c) => String(c.ch) === String(r.chapter))).map((r: any) => (
          <ChapterRow key={r.id} label={`Ch.${r.chapter}`} row={r} />
        ))}
        {list.map((c) => {
          const r = made[String(c.ch)];
          return r ? <ChapterRow key={c.ch} label={`Ch.${c.ch}`} date={c.date} row={r} /> : (
            <div className="it" key={c.ch}>
              <div className="row"><b>Ch.{c.ch}</b><span className="small muted">{c.date || ""}</span><StatusPill s={{ key: "found", label: "Not made", tone: "muted" }} /></div>
              <div className="row">
                <ConfirmButton className="sm" confirm="Make it? (~$0.50)" onConfirm={() => act(async () => { await api("/api/autopilot/run", { series_id: s.id, chapter: String(c.ch) }); reload(); }, `Making ch.${c.ch}`)}>Make</ConfirmButton>
                <ConfirmButton className="sm ghost" confirm={`Plan from ch.${c.ch}?`} title="autopilot makes this chapter and every one after it, in order"
                  onConfirm={() => act(async () => { await api("/api/autopilot/backfill", { series_id: s.id, from_chapter: String(c.ch) }); reload(); }, `Planned from ch.${c.ch}`)}>Plan from here</ConfirmButton>
              </div>
            </div>
          );
        })}
        {src.loading && <Empty>Loading chapters…</Empty>}
      </div>
    </div>
  );
}

function ChapterRow({ label, row, date }: { label: string; row: any; date?: string }) {
  return (
    <div className="it">
      <div className="row"><b>{label}</b>{date && <span className="small muted">{date}</span>}<StatusPill s={row.status} />{row.auto && <span className="small faint">autopilot</span>}</div>
      <Link className="btn sm" href={`/chapter/${row.id}`}>Open →</Link>
    </div>
  );
}

function Cast({ s }: { s: any }) {
  const { data, reload } = useApi<any>(`/api/series/bible?series_id=${enc(s.id)}`);
  const act = useAct();
  const [edit, setEdit] = useState<any[] | null>(null);
  const b = data?.bible;
  const chars: any[] = b?.characters || [];
  return (
    <div style={{ borderTop: "1px solid var(--border)", padding: 14, display: "grid", gap: 10 }}>
      {!data ? <span className="muted">Loading…</span> : !b ? <span className="muted">No cast list yet.</span> : edit ? (
        <>
          {edit.map((c, i) => (
            <div key={i} className="grid" style={{ gridTemplateColumns: "1.2fr 1.6fr .8fr 1.2fr auto", gap: 6 }}>
              <input value={c.name || ""} placeholder="Name" onChange={(e) => setEdit(edit.map((x, j) => j === i ? { ...x, name: e.target.value } : x))} />
              <input value={(c.aliases || []).join(", ")} placeholder="Other names, comma separated" onChange={(e) => setEdit(edit.map((x, j) => j === i ? { ...x, aliases: e.target.value.split(",").map((a) => a.trim()).filter(Boolean) } : x))} />
              <input value={c.pronouns || ""} placeholder="he/him" onChange={(e) => setEdit(edit.map((x, j) => j === i ? { ...x, pronouns: e.target.value } : x))} />
              <input value={c.role || ""} placeholder="Role" onChange={(e) => setEdit(edit.map((x, j) => j === i ? { ...x, role: e.target.value } : x))} />
              <button className="sm danger" onClick={() => setEdit(edit.filter((_, j) => j !== i))} aria-label="Remove">✕</button>
            </div>
          ))}
          <div className="row">
            <button className="sm" onClick={() => setEdit([...edit, { name: "", aliases: [], pronouns: "", role: "" }])}>+ Add character</button>
            <Busy className="sm primary" onClick={() => act(async () => { await api("/api/series/bible", { series_id: s.id, bible: { ...b, characters: edit.filter((c) => c.name.trim()) } }); setEdit(null); reload(); }, "Cast saved")}>Save</Busy>
            <button className="sm ghost" onClick={() => setEdit(null)}>Cancel</button>
          </div>
        </>
      ) : (
        <>
          <div className="list">
            {chars.map((c, i) => (
              <div className="it" key={i} style={{ padding: "6px 0" }}>
                <div><b>{c.name}</b>{c.aliases?.length ? <span className="small muted"> · {c.aliases.join(", ")}</span> : null}</div>
                <span className="small muted">{[c.pronouns, c.role].filter(Boolean).join(" · ")}</span>
              </div>
            ))}
          </div>
          {(b.research?.disputes || []).length > 0 && (
            <details><summary className="small muted">{b.research.disputes.length} things sources disagree on</summary>
              <ul className="small muted">{b.research.disputes.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></details>
          )}
          {(b.suggested_characters || []).length > 0 && <div className="small muted">Names the scripts use that aren’t listed: {b.suggested_characters.join(", ")}</div>}
          <div className="small faint">Researched {b.research?.at ? when(b.research.at) : "—"} · {(b.research?.sources || []).length} sources</div>
        </>
      )}
      {!edit && (
        <div className="row">
          {b && <button className="sm" onClick={() => setEdit(JSON.parse(JSON.stringify(chars)))}>✎ Edit</button>}
          <ConfirmButton className="sm" confirm="Research again? (~$0.05)" onConfirm={() => act(async () => { await api("/api/series/research", { series_id: s.id, missing_only: false }); }, "Researching — about 4 minutes")}>
            ↻ Research again
          </ConfirmButton>
        </div>
      )}
    </div>
  );
}
