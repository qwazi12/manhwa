"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, mmss, when } from "@/lib/fmt";
import { Busy, ConfirmButton, Empty, PageHead, useAct, useToast } from "@/components/ui";

/** Posting schedule — laid out like Scrapper's SocialPilot (owner, 2026-10-06,
 *  side-by-side of QueuePanel.tsx): one header card (counts · ↻ Live · labelled
 *  Undo · ⏸ Pause posting / Sort ↑↓ · 🔀 Mix ▾ · ⏰ Schedule ▾), one scrolling row
 *  of status tabs, "Select all", then a CARD per video: full-width thumbnail,
 *  status badge, title, the planned post time, the channel, and a row of
 *  actions. */
type Tab = "review" | "ready" | "posted" | "errors" | "all";
type Sort = "planned" | "title" | "added";

const BADGE: Record<string, [string, string]> = {
  review: ["👁 Needs review", "info"], ready: ["● Ready to post", "ok"], posting: ["⏳ Posting", "warn"],
  error: ["⚠ Failed", "bad"], posted: ["✓ Posted", "ok"],
};

export default function PostingSchedule() {
  const studio = useApi<any>("/api/studio", 15000);
  const ready = useApi<any>("/api/chapters?status=video_ready", 15000);
  const all = useApi<any>("/api/chapters", 60000);
  const acc = useApi<any>("/api/publishing/status");
  const act = useAct();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("review");
  const [sort, setSort] = useState<Sort>("planned");
  const [desc, setDesc] = useState(false);
  const [sel, setSel] = useState<string[]>([]);
  const [menu, setMenu] = useState<"" | "mix" | "schedule">("");
  const [pick, setPick] = useState<any[] | null>(null);
  const [editing, setEditing] = useState<any[] | null>(null);
  const [stats, setStats] = useState<Record<string, any>>({});
  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as Tab; if (t) setTab(t === ("scheduled" as any) ? "ready" : t); } catch {}
  }, []);
  useEffect(() => { setSel([]); }, [tab]);

  const s = studio.data;
  const sched = s?.schedule || {};
  const sidOf: Record<string, string> = {};
  for (const r of all.data?.chapters || []) if (r.series_id) sidOf[r.id] = r.series_id;
  const accounts: any[] = (acc.data?.accounts || []).filter((a: any) => a.active);
  const chanName = (id: string) => { const a = accounts.find((x) => x.account_id === id); return a ? `${a.username || id}${a.platform ? ` (${a.platform})` : ""}` : id; };
  const reload = () => { studio.reload(); ready.reload(); };

  const reviewRows = (ready.data?.chapters || []).map((r: any) => ({
    key: `${r.id}|${r.video}`, kind: "review", project: r.id, name: r.video, title: r.title, label: r.title, sid: r.series_id, superseded: r.status?.superseded,
  }));
  const qRows = (s?.queue || []).map((r: any) => ({ ...r, key: `${r.project}|${r.name}`, kind: r.qstatus === "failed" ? "error" : r.qstatus === "posting" ? "posting" : "ready", sid: sidOf[r.project] }));
  const readyRows = qRows.filter((r: any) => r.kind !== "error");
  const errorRows = qRows.filter((r: any) => r.kind === "error");
  const postedRows = (s?.published || []).map((p: any) => ({ ...p, key: `${p.project}|${p.name}`, kind: "posted", sid: sidOf[p.project] }));
  const tabs: [Tab, string, any[]][] = [
    ["review", "👁 Needs review", reviewRows], ["ready", "● Ready to post", readyRows], ["posted", "✓ Posted", postedRows],
    ["errors", "⚠ Errors", errorRows], ["all", "All", [...reviewRows, ...readyRows, ...errorRows, ...postedRows]],
  ];
  const total = reviewRows.length + readyRows.length + errorRows.length + postedRows.length;
  const rows = useMemo(() => {
    const r = [...(tabs.find((t) => t[0] === tab)?.[2] || [])];
    if (sort === "title") r.sort((a, b) => String(a.title || a.label).localeCompare(String(b.title || b.label)));
    if (sort === "added") r.sort((a, b) => (a.added_at || a.at || 0) - (b.added_at || b.at || 0));
    return desc ? r.reverse() : r;               // "planned" = queue order = the order they post
  }, [s, ready.data, tab, sort, desc]);
  const picked = rows.filter((r) => sel.includes(r.key));

  const bulk = (action: string, items: any[], extra: any = {}, ok?: string) => act(async () => {
    const r = await api("/api/studio/bulk", { action, items: items.map((x) => ({ project: x.project, name: x.name, qid: x.qid })), ...extra });
    setSel([]); reload();
    if (r.failed?.length) toast(`${r.done.length} done · not done: ${r.failed.map((f: any) => `${f.project} (${f.reason})`).join("; ")}`, true);
    else if (ok) toast(ok);
  });

  return (
    <>
      <PageHead title="Posting schedule" sub="Watch, approve, and each video takes the next posting time." />
      <div className="card sp-head">
        <div className="sp-row">
          <b>📋 Posting queue</b>
          <span className="pill t-muted">{rows.length} shown · {total} total</span>
          <button className="sm ghost" onClick={reload}>↻ Live</button>
          {s?.undo_label && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/studio/queue/undo", {}); reload(); }, "Undone")}>↶ Undo: {s.undo_label}</Busy>}
          <Busy className={`sm ${sched.enabled ? "" : "primary"}`} onClick={() => act(async () => { await api("/api/settings", { schedule: { enabled: !sched.enabled } }); reload(); },
            sched.enabled ? "Posting paused — nothing posts until you resume" : "Posting resumed")}>{sched.enabled ? "⏸ Pause posting" : "▶ Resume posting"}</Busy>
        </div>
        <div className="sp-row">
          <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} aria-label="Sort" style={{ width: "auto" }}>
            <option value="planned">Sort: Posting order</option><option value="title">Sort: Title</option><option value="added">Sort: Date added</option></select>
          <button className="sm" aria-label="Direction" onClick={() => setDesc(!desc)}>{desc ? "↓" : "↑"}</button>
          <span style={{ position: "relative" }}>
            <button className="sm sp-mix" disabled={readyRows.length < 2} onClick={() => setMenu(menu === "mix" ? "" : "mix")}>🔀 Mix ▾</button>
            {menu === "mix" && (
              <div className="card sp-pop">
                {[["round_robin", "🔄 Round-robin (recommended)", "one video from each series in turn"], ["by_series", "📚 By series", "each series together, in chapter order"],
                  ["random", "🎲 Random", "shuffled; chapters stay in order"]].map(([m, l, d]) => (
                  <button key={m} className={`sm ${m === "round_robin" ? "primary" : ""}`} style={{ textAlign: "left" }}
                    onClick={() => { setMenu(""); act(async () => { await api("/api/studio/queue/shuffle", { mode: m }); reload(); }, "Order mixed — Undo puts it back"); }}>
                    {l}<br /><span className="small">{d}</span></button>))}
              </div>)}
          </span>
          <span style={{ position: "relative" }}>
            <button className="sm sp-sched" onClick={() => setMenu(menu === "schedule" ? "" : "schedule")}>⏰ Schedule · {(sched.times || []).length}/day ▾</button>
            {menu === "schedule" && <SchedulePop sched={sched} plannedNext={readyRows.filter((r: any) => r.planned).slice(0, 4)} onDone={() => { setMenu(""); reload(); }} />}
          </span>
        </div>
        <div className="small muted">{sched.enabled ? <>Next post <b>{sched.next || "—"}</b> ET · up to {sched.per_channel_per_day} a day per channel</> : <b>Posting is paused — nothing posts until you resume.</b>}</div>
      </div>

      <div className="sp-tabs" role="tablist">
        {tabs.map(([k, l, r]) => (
          <button key={k} role="tab" aria-selected={tab === k} className={`sp-tab${tab === k ? " on" : ""}`} onClick={() => setTab(k)}>
            {l} <span className="sp-count">{r.length}</span></button>))}
      </div>

      <div className="sp-row" style={{ padding: "4px 2px" }}>
        <label className="check small"><input type="checkbox" checked={rows.length > 0 && picked.length === rows.length}
          onChange={(e) => setSel(e.target.checked ? rows.map((r) => r.key) : [])} /> Select all {rows.length}</label>
        {tab === "errors" && errorRows.length > 0 && <ConfirmButton className="sm" confirm={`Put all ${errorRows.length} failed video(s) back in line? Each takes the next free posting time.`} onConfirm={() => bulk("requeue", errorRows, {}, "Back in line — they take the next posting times")}>↩ Put all failed back in line</ConfirmButton>}
      </div>

      {picked.length > 0 && (
        <div className="card sp-bulk">
          <b className="small">{picked.length} selected</b>
          {tab === "review" && <ConfirmButton className="sm primary" confirm={`Approve ${picked.length}?`} onConfirm={() => bulk("approve", picked, {}, "Approved — they take the next posting times")}>✓ Approve</ConfirmButton>}
          {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm" confirm="Back to Needs review?" onConfirm={() => bulk("back", picked, {}, "Back in Needs review")}>↩ To review</ConfirmButton>}
          {tab === "errors" && <ConfirmButton className="sm primary" confirm={`Put ${picked.length} back in line?`} onConfirm={() => bulk("requeue", picked, {}, "Back in line")}>↩ Back in line</ConfirmButton>}
          <button className="sm" onClick={() => setEditing(picked)}>✏️ Mass edit</button>
          <select className="sm" value="" aria-label="Set privacy" onChange={(e) => e.target.value && bulk("edit", picked, { privacy: e.target.value }, `Privacy set to ${e.target.value}`)}>
            <option value="">🔒 Privacy…</option><option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select>
          <button className="sm" onClick={() => setPick(picked)}>📺 Channels</button>
          <ConfirmButton className="sm" confirm={`Redo SEO for ${picked.length}? (a few cents each)`} onConfirm={() => bulk("seo", picked, {}, "Redoing SEO — titles update in a minute")}>✨ AI</ConfirmButton>
          {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm" confirm={`Post ${picked.length} now?`} onConfirm={() => bulk("post_now", picked, {}, "Posting")}>🚀 Post now</ConfirmButton>}
          {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm danger" confirm={`Take ${picked.length} off the schedule?`} onConfirm={() => bulk("remove", picked, {}, "Removed")}>🗑</ConfirmButton>}
          <button className="sm ghost" onClick={() => setSel([])}>Clear</button>
        </div>
      )}

      {!s ? <Empty>Loading…</Empty> : rows.length === 0 ? (
        <Empty>{tab === "review" ? "No videos waiting for review. Rendered chapters appear here." : tab === "ready" ? "Nothing scheduled. Approve a video and it lands here with its post time." : tab === "errors" ? "✓ No failed posts — everything is healthy." : "Nothing here yet."}</Empty>
      ) : (
        <div className="sp-cards">
          {rows.map((r: any, i: number) => (
            <Card key={r.key + r.kind} r={r} i={i} sched={sched} chanName={chanName} stats={stats}
              checked={sel.includes(r.key)} onCheck={(on: boolean) => setSel(on ? [...sel, r.key] : sel.filter((k) => k !== r.key))}
              onChannels={() => setPick([r])} reload={reload} bulk={bulk} />
          ))}
        </div>
      )}
      {tab === "posted" && s?.yt_stats && postedRows.length > 0 && (
        <Busy className="sm" onClick={() => act(async () => {
          const ids = postedRows.flatMap((p: any) => p.posts.map((x: any) => x.video_id)).filter(Boolean);
          const r = await api("/api/studio/stats", { video_ids: ids }); setStats(r.stats || {});
        })}>📈 Load YouTube views</Busy>
      )}
      {editing && <MassEdit items={editing} onClose={() => setEditing(null)}
        onSave={(f: any) => { const items = editing; setEditing(null); bulk("edit", items, f, `Saved for ${items.length} video(s)`); }} />}
      {pick && <ChannelPicker items={pick} accounts={accounts} onClose={() => setPick(null)}
        onSave={(targets) => { const items = pick; setPick(null); bulk("channels", items, { targets }, "Channels saved"); }} />}
    </>
  );
}

function SchedulePop({ sched, plannedNext, onDone }: any) {
  const act = useAct();
  const [times, setTimes] = useState<string[]>(sched.times || []);
  const [per, setPer] = useState<number>(sched.per_channel_per_day || 1);
  const [add, setAdd] = useState("12:00");
  return (
    <div className="card sp-pop" style={{ width: 300 }}>
      <b className="small">Posting times (Eastern)</b>
      <div className="row" style={{ gap: 6 }}>
        {times.map((t) => <span key={t} className="pill t-muted">{t} <button className="sm ghost" style={{ padding: "0 4px" }} aria-label={`Remove ${t}`} onClick={() => setTimes(times.filter((x) => x !== t))}>✕</button></span>)}
      </div>
      <div className="row" style={{ gap: 6 }}>
        <input type="time" value={add} onChange={(e) => setAdd(e.target.value)} style={{ width: 120 }} aria-label="Add a posting time" />
        <button className="sm" disabled={!add || times.includes(add) || times.length >= 6} onClick={() => setTimes([...times, add].sort())}>＋ Add</button>
      </div>
      <label className="small">Posts per channel a day <select value={per} onChange={(e) => setPer(Number(e.target.value))} style={{ width: "auto" }}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></label>
      {plannedNext.length > 0 && <div className="small muted">Next: {plannedNext.map((r: any) => r.planned.label.replace(" ET", "")).join(" · ")}</div>}
      <div className="row">
        <Busy className="sm primary" disabled={!times.length} onClick={() => act(async () => { await api("/api/settings", { schedule: { times, per_channel_per_day: per } }); onDone(); }, "Schedule saved — ready videos moved onto the new times")}>Save</Busy>
        <button className="sm ghost" onClick={onDone}>Close</button>
      </div>
    </div>
  );
}

function Card({ r, i, sched, chanName, stats, checked, onCheck, onChannels, reload, bulk }: any) {
  const act = useAct();
  const [b, tone] = BADGE[r.kind] || [r.kind, "muted"];
  const targets: string[] = r.targets || [];
  const href = `/chapter/${enc(r.project)}?tab=video`;
  return (
    <div className={`card sp-card${checked ? " sel" : ""}`}>
      <div className="sp-top">
        <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} aria-label="Select" />
        <span className={`pill t-${tone}`}>{b}</span>
      </div>
      <Link href={href} className="sp-thumb"><Pic project={r.project} name={r.name} sid={r.sid} thumb={r.thumb} /></Link>
      <b className="sp-title">{r.title || r.label}</b>
      <span className="small muted">{r.label !== r.title ? `${r.label} · ` : ""}{r.duration ? `${mmss(r.duration)}` : ""}{r.missing ? " · the video file is gone" : ""}
        {r.kind === "ready" || r.kind === "error" ? (
          <select className="sm" style={{ marginLeft: 6 }} value={r.privacy || "private"} aria-label="Privacy"
            onChange={(e) => bulk("edit", [r], { privacy: e.target.value }, `Privacy: ${e.target.value}`)}>
            <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select>
        ) : r.privacy ? ` · ${r.privacy}` : ""}</span>
      {r.kind === "ready" && r.blocked && <span className="small" style={{ color: "var(--red)", wordBreak: "break-word" }}>⚠ won’t post until fixed: {r.blocked} — its slot goes to the next video</span>}
      {r.kind === "ready" && !r.blocked && (r.planned ? <span className="sp-time">⏰ {r.planned.label}</span>
        : <span className="small" style={{ color: "var(--yellow)" }}>{!sched.enabled ? "⏸ waits — posting is paused" : targets.length ? `#${i + 1} in line` : "no channel, so no time"}</span>)}
      {r.kind === "posting" && <span className="sp-time">⏳ uploading now…</span>}
      {r.kind === "posted" && <span className="sp-time">✓ posted {when(r.at)}</span>}
      {r.kind === "error" && <span className="small" style={{ color: "var(--red)", wordBreak: "break-word" }}>⚠ {r.qerror || r.last_error}</span>}
      {r.superseded && <span className="small" style={{ color: "var(--yellow)" }}>the cut changed after approval</span>}
      <div className="row" style={{ gap: 4 }}>
        {r.kind === "posted" ? (r.posts || []).map((x: any) => <span key={x.account_id} className="pill t-muted">📺 {x.username || x.account_id}</span>)
          : r.kind === "review" ? null
          : targets.length ? targets.map((t) => <button key={t} className="pill t-info" style={{ border: 0 }} onClick={onChannels}>📺 {chanName(t)} ✎</button>)
          : <button className="sm" style={{ borderStyle: "dashed", color: "var(--yellow)" }} onClick={onChannels}>⚠ No channel — choose</button>}
      </div>
      <div className="sp-actions">
        {r.kind === "review" && <ConfirmButton className="sm primary" confirm="Approve and schedule?" onConfirm={() => bulk("approve", [r], {}, "Approved — it takes the next posting time")}>✓ Approve</ConfirmButton>}
        {r.kind === "ready" && i > 0 && <Busy className="sm" onClick={() => act(async () => { await api("/api/studio/queue/top", { id: r.qid }); reload(); }, "It posts next — Undo puts it back")}>⏫ Post next</Busy>}
        {r.kind === "error" && <ConfirmButton className="sm primary" confirm="Put it back in line? It takes the next free posting time." onConfirm={() => bulk("requeue", [r], {}, "Back in line")}>↩ Back in line</ConfirmButton>}
        {(r.kind === "ready" || r.kind === "error") && <ConfirmButton className="sm" confirm={`Post now (${r.privacy})?`} disabled={r.missing} onConfirm={() => bulk("post_now", [r], {}, "Posting")}>🚀 Post now</ConfirmButton>}
        {r.kind !== "posted" && <ConfirmButton className="sm" confirm="Redo the title, description and tags? (a few cents)" onConfirm={() => bulk("seo", [r], {}, "Redoing SEO — updates in a minute")}>✨ AI</ConfirmButton>}
        {r.kind !== "posted" && <Link className="btn sm" href={href}>✎ Edit</Link>}
        {r.kind === "posted" ? (r.posts || []).map((x: any) => x.url && <a key={x.account_id} className="btn sm" href={x.url} target="_blank" rel="noreferrer">▶ YouTube{stats[x.video_id] ? ` · ${stats[x.video_id].views.toLocaleString()} views` : ""}</a>)
          : <Link className="btn sm" href={href}>▶ Watch</Link>}
        {(r.kind === "ready" || r.kind === "error") && <ConfirmButton className="sm" confirm="Back to Needs review?" onConfirm={() => bulk("back", [r], {}, "Back in Needs review")}>👁 To review</ConfirmButton>}
        {(r.kind === "ready" || r.kind === "error") && <ConfirmButton className="sm danger" confirm="Take it off the schedule?" onConfirm={() => bulk("remove", [r], {}, "Removed")}>🗑</ConfirmButton>}
      </div>
    </div>
  );
}

/** Scrapper's Mass Edit: only the fields you fill in are changed. */
function MassEdit({ items, onClose, onSave }: { items: any[]; onClose: () => void; onSave: (f: any) => void }) {
  const [title, setTitle] = useState("");
  const [description, setDescription] = useState("");
  const [tags, setTags] = useState("");
  const [privacy, setPrivacy] = useState("");
  const f: any = {};
  if (title.trim()) f.title = title.trim();
  if (description.trim()) f.description = description.trim();
  if (tags.trim()) f.tags = tags.split(",").map((t) => t.trim()).filter(Boolean);
  if (privacy) f.privacy = privacy;
  return (
    <div role="dialog" aria-label="Mass edit" style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(0,0,0,.55)", display: "grid", placeItems: "center", padding: 16 }} onClick={onClose}>
      <div className="card" style={{ width: "min(560px, 100%)", display: "grid", gap: 10, padding: 16 }} onClick={(e) => e.stopPropagation()}>
        <b>Edit {items.length} video{items.length === 1 ? "" : "s"} at once</b>
        <span className="small muted">Only what you fill in changes; empty fields keep each video’s own text.</span>
        <label className="field">Title<input value={title} maxLength={100} onChange={(e) => setTitle(e.target.value)} placeholder="(keep each video’s title)" /></label>
        <label className="field">Description<textarea value={description} onChange={(e) => setDescription(e.target.value)} placeholder="(keep each video’s description)" style={{ minHeight: 90 }} /></label>
        <label className="field">Tags (comma separated)<input value={tags} onChange={(e) => setTags(e.target.value)} placeholder="(keep each video’s tags)" /></label>
        <label className="field">Privacy<select value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
          <option value="">(keep each video’s privacy)</option><option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select></label>
        <div className="row"><button className="sm primary" disabled={!Object.keys(f).length} onClick={() => onSave(f)}>Save for {items.length}</button>
          <button className="sm ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}

function ChannelPicker({ items, accounts, onClose, onSave }: { items: any[]; accounts: any[]; onClose: () => void; onSave: (t: string[]) => void }) {
  const [t, setT] = useState<string[]>(items[0]?.targets || []);
  return (
    <div role="dialog" aria-label="Choose channels" style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(0,0,0,.55)", display: "grid", placeItems: "center", padding: 16 }} onClick={onClose}>
      <div className="card" style={{ width: "min(420px, 100%)", display: "grid", gap: 10, padding: 16 }} onClick={(e) => e.stopPropagation()}>
        <b>Where should {items.length === 1 ? "this video" : `these ${items.length} videos`} post?</b>
        {accounts.length === 0 ? <span className="small muted">No channels connected — connect one in Settings.</span> : accounts.map((a) => (
          <label key={a.account_id} className="check"><input type="checkbox" checked={t.includes(a.account_id)}
            onChange={(e) => setT(e.target.checked ? [...t, a.account_id] : t.filter((x) => x !== a.account_id))} /> {a.username || a.account_id} <span className="faint small">{a.platform || ""}</span></label>
        ))}
        <div className="row"><button className="sm primary" onClick={() => onSave(t)}>Save</button><button className="sm ghost" onClick={onClose}>Cancel</button></div>
      </div>
    </div>
  );
}

/** The video's own thumbnail, else the series cover. */
function Pic({ project, name, sid, thumb }: { project: string; name?: string | null; sid?: string | null; thumb?: string | null }) {
  const [src, setSrc] = useState<string | null>(thumb || (name ? `/thumbnail?project=${enc(project)}&name=${enc(name)}` : null) || (sid ? `/api/watchlist/cover/${enc(sid)}` : null));
  const [triedCover, setTriedCover] = useState(!name && !thumb);
  return src ? (
    <img src={src} alt="" loading="lazy" onError={() => { if (!triedCover && sid) { setTriedCover(true); setSrc(`/api/watchlist/cover/${enc(sid)}`); } else setSrc(null); }} />
  ) : <div className="sp-ph" />;
}
