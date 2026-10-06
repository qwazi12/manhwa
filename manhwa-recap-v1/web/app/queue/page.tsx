"use client";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, mmss, when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, PageHead, Pill, StatusPill, useAct, useToast } from "@/components/ui";

/** Posting schedule — built like Scrapper's SocialPilot (owner, 2026-10-06):
 *  header with the schedule switch and times, status tabs with counts, sort,
 *  Mix & Shuffle with Undo, tick rows for bulk actions, and image rows that
 *  show each video's channel and planned post time. */
type Tab = "review" | "ready" | "posted" | "errors" | "all";
type Sort = "planned" | "title" | "series" | "added";

export default function PostingSchedule() {
  const studio = useApi<any>("/api/studio", 15000);
  const ready = useApi<any>("/api/chapters?status=video_ready", 15000);
  const all = useApi<any>("/api/chapters", 60000);
  const acc = useApi<any>("/api/publishing/status");
  const act = useAct();
  const toast = useToast();
  const [tab, setTab] = useState<Tab>("review");
  const [sort, setSort] = useState<Sort>("planned");
  const [sel, setSel] = useState<string[]>([]);
  const [mixOpen, setMixOpen] = useState(false);
  const [pick, setPick] = useState<any[] | null>(null);     // items waiting for a channel choice
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
  const nameOf = (id: string) => accounts.find((a) => a.account_id === id)?.username || id;
  const reload = () => { studio.reload(); ready.reload(); };

  // one row shape for every tab
  const reviewRows = (ready.data?.chapters || []).map((r: any) => ({
    key: `${r.id}|${r.video}`, kind: "review", project: r.id, name: r.video, title: r.title, label: r.title,
    sid: r.series_id, status: r.status, superseded: r.status?.superseded,
  }));
  const qRows = (s?.queue || []).map((r: any) => ({ ...r, key: `${r.project}|${r.name}`, kind: r.qstatus === "failed" ? "error" : "ready", sid: sidOf[r.project] }));
  const readyRows = qRows.filter((r: any) => r.kind === "ready");
  const errorRows = qRows.filter((r: any) => r.kind === "error");
  const postedRows = (s?.published || []).map((p: any) => ({ ...p, key: `${p.project}|${p.name}`, kind: "posted", sid: sidOf[p.project] }));
  const tabs: [Tab, string, any[]][] = [
    ["review", "👁 Needs review", reviewRows], ["ready", "● Ready to post", readyRows],
    ["posted", "✓ Posted", postedRows], ["errors", "⚠ Errors", errorRows],
    ["all", "All", [...reviewRows, ...readyRows, ...errorRows, ...postedRows]],
  ];
  const rows = useMemo(() => {
    const r = [...(tabs.find((t) => t[0] === tab)?.[2] || [])];
    if (sort === "title") r.sort((a, b) => String(a.title || a.label).localeCompare(String(b.title || b.label)));
    if (sort === "series") r.sort((a, b) => String(a.label).localeCompare(String(b.label)));
    if (sort === "added") r.sort((a, b) => (b.added_at || b.at || 0) - (a.added_at || a.at || 0));
    return r;                                     // "planned" = queue order = post order
  }, [s, ready.data, tab, sort]);
  const picked = rows.filter((r) => sel.includes(r.key));

  const bulk = (action: string, items: any[], extra: any = {}, ok?: string) => act(async () => {
    const r = await api("/api/studio/bulk", { action, items: items.map((x) => ({ project: x.project, name: x.name, qid: x.qid })), ...extra });
    setSel([]); reload();
    if (r.failed?.length) toast(`${r.done.length} done · not done: ${r.failed.map((f: any) => `${f.project} (${f.reason})`).join("; ")}`, true);
    else if (ok) toast(ok);
  });
  const toggleSchedule = () => act(async () => { await api("/api/settings", { schedule: { enabled: !sched.enabled } }); reload(); },
    sched.enabled ? "Auto-posting paused — nothing posts until you resume" : "Auto-posting resumed");

  return (
    <>
      <PageHead title="Posting schedule" sub="Watch, approve, and each video takes the next posting time. Like SocialPilot: tick rows for bulk actions, Mix the order, see when each one posts." />
      <ScheduleHeader s={s} sched={sched} onToggle={toggleSchedule} onSaved={reload}
        counts={{ review: reviewRows.length, ready: readyRows.length, errors: errorRows.length }} />

      <Chips<Tab> value={tab} onChange={setTab} items={tabs.map(([k, l, r]) => [k, `${l} (${r.length})`] as [Tab, string])} />

      <Card pad={false}>
        <div className="spread" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", gap: 8 }}>
          <label className="check small"><input type="checkbox" checked={rows.length > 0 && picked.length === rows.length}
            onChange={(e) => setSel(e.target.checked ? rows.map((r) => r.key) : [])} /> Select all ({rows.length})</label>
          <div className="row">
            <select value={sort} onChange={(e) => setSort(e.target.value as Sort)} style={{ width: "auto" }} aria-label="Sort">
              <option value="planned">Sort: post order</option><option value="title">Sort: title</option>
              <option value="series">Sort: series</option><option value="added">Sort: newest added</option>
            </select>
            {tab === "ready" && (
              <span style={{ position: "relative" }}>
                <button className="sm" disabled={readyRows.length < 2} onClick={() => setMixOpen(!mixOpen)}>🔀 Mix ▾</button>
                {mixOpen && (
                  <div className="card" style={{ position: "absolute", right: 0, top: "110%", zIndex: 30, width: 260, display: "grid", gap: 6, padding: 10 }}>
                    {[["round_robin", "🔄 Round-robin (recommended)", "one video from each series in turn"], ["by_series", "📚 By series", "each series together"],
                      ["random", "🎲 Random", "shuffled; chapters stay in order"]].map(([m, l, d]) => (
                      <button key={m} className={`sm ${m === "round_robin" ? "primary" : ""}`} onClick={() => { setMixOpen(false); act(async () => { await api("/api/studio/queue/shuffle", { mode: m }); reload(); }, "Order mixed"); }}>
                        {l}<br /><span className="small">{d}</span></button>
                    ))}
                  </div>
                )}
              </span>
            )}
            {tab === "ready" && s?.can_undo_order && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/studio/queue/undo", {}); reload(); }, "Order restored")}>↶ Undo</Busy>}
            {tab === "errors" && errorRows.length > 0 && <ConfirmButton className="sm" confirm={`Try all ${errorRows.length} again?`} onConfirm={() => bulk("post_now", errorRows, {}, "Trying again")}>↻ Retry all</ConfirmButton>}
            <Busy className="sm ghost" onClick={async () => reload()}>↻</Busy>
          </div>
        </div>

        {picked.length > 0 && (
          <div className="row" style={{ padding: "8px 14px", borderBottom: "1px solid var(--border)", background: "var(--panel2)", gap: 6 }}>
            <b className="small">{picked.length} selected:</b>
            {tab === "review" && <ConfirmButton className="sm primary" confirm={`Approve ${picked.length} and schedule them?`} onConfirm={() => bulk("approve", picked, {}, "Approved — they take the next posting times")}>✓ Approve</ConfirmButton>}
            {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm" confirm="Back to Needs review?" onConfirm={() => bulk("back", picked, {}, "Back in Needs review")}>↩ Back to review</ConfirmButton>}
            <button className="sm" onClick={() => setPick(picked)}>📺 Channels…</button>
            <ConfirmButton className="sm" confirm={`Redo SEO for ${picked.length}? (a few cents each)`} onConfirm={() => bulk("seo", picked, {}, "Redoing SEO — titles update in a minute")}>✨ Redo SEO</ConfirmButton>
            {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm" confirm={`Post ${picked.length} now?`} onConfirm={() => bulk("post_now", picked, {}, "Posting")}>🚀 Post now</ConfirmButton>}
            {(tab === "ready" || tab === "errors") && <ConfirmButton className="sm danger" confirm={`Take ${picked.length} off the schedule?`} onConfirm={() => bulk("remove", picked, {}, "Removed")}>✕ Remove</ConfirmButton>}
            <button className="sm ghost" onClick={() => setSel([])}>Clear</button>
          </div>
        )}

        {!s ? <Empty>Loading…</Empty> : rows.length === 0 ? (
          <Empty>{tab === "review" ? "No videos waiting for review. Rendered chapters appear here." : tab === "ready" ? "Nothing scheduled. Approve a video and it lands here with its post time." : tab === "errors" ? "✓ No failed posts." : "Nothing here yet."}</Empty>
        ) : (
          <div className="list">
            {rows.map((r: any, i: number) => (
              <Row key={r.key + r.kind} r={r} i={i} sched={sched} nameOf={nameOf} stats={stats} retention={s.retention_days}
                checked={sel.includes(r.key)} onCheck={(on: boolean) => setSel(on ? [...sel, r.key] : sel.filter((k) => k !== r.key))}
                onChannels={() => setPick([r])} reload={reload} bulk={bulk} />
            ))}
          </div>
        )}
        {tab === "posted" && s?.yt_stats && postedRows.length > 0 && (
          <div style={{ padding: "8px 14px" }}><Busy className="sm" onClick={() => act(async () => {
            const ids = postedRows.flatMap((p: any) => p.posts.map((x: any) => x.video_id)).filter(Boolean);
            const r = await api("/api/studio/stats", { video_ids: ids }); setStats(r.stats || {});
          })}>📈 Load YouTube views</Busy></div>
        )}
      </Card>

      {pick && <ChannelPicker items={pick} accounts={accounts} onClose={() => setPick(null)}
        onSave={(targets) => { const items = pick; setPick(null); bulk("channels", items, { targets }, "Channels saved"); }} />}
    </>
  );
}

function ScheduleHeader({ s, sched, counts, onToggle, onSaved }: any) {
  const act = useAct();
  const [times, setTimes] = useState<string[]>([]);
  const [per, setPer] = useState(1);
  const [add, setAdd] = useState("12:00");
  useEffect(() => { setTimes(sched.times || []); setPer(sched.per_channel_per_day || 1); }, [sched.times?.join(","), sched.per_channel_per_day]);
  const dirty = times.join(",") !== (sched.times || []).join(",") || per !== (sched.per_channel_per_day || 1);
  return (
    <Card>
      <div className="spread">
        <div className="row">
          <Pill tone={sched.enabled ? "ok" : "warn"}>{sched.enabled ? "● Auto-posting on" : "⏸ Auto-posting paused"}</Pill>
          <span className="small">{sched.enabled ? <>next post <b>{sched.next || "—"}</b> ET</> : "nothing posts until you resume"} · {counts.review} to review · {counts.ready} scheduled{counts.errors ? ` · ${counts.errors} failed` : ""}</span>
        </div>
        <Busy className={`sm ${sched.enabled ? "" : "primary"}`} onClick={async () => onToggle()}>{sched.enabled ? "⏸ Pause auto-posting" : "▶ Resume auto-posting"}</Busy>
      </div>
      <div className="row" style={{ marginTop: 8, gap: 6 }}>
        <span className="small muted">Posting times (ET):</span>
        {times.map((t) => <span key={t} className="pill t-muted">{t} <button className="sm ghost" style={{ padding: "0 4px" }} aria-label={`Remove ${t}`} onClick={() => setTimes(times.filter((x) => x !== t))}>✕</button></span>)}
        <input type="time" value={add} onChange={(e) => setAdd(e.target.value)} style={{ width: 110 }} aria-label="Add a posting time" />
        <button className="sm ghost" disabled={!add || times.includes(add) || times.length >= 6} onClick={() => setTimes([...times, add].sort())}>＋ Add</button>
        <label className="small">per channel a day <select value={per} onChange={(e) => setPer(Number(e.target.value))} style={{ width: "auto" }}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></label>
        {dirty && <Busy className="sm primary" onClick={() => act(async () => { await api("/api/settings", { schedule: { times, per_channel_per_day: per } }); onSaved(); }, "Schedule saved")}>Save</Busy>}
      </div>
    </Card>
  );
}

function Row({ r, i, sched, nameOf, stats, retention, checked, onCheck, onChannels, reload, bulk }: any) {
  const act = useAct();
  const targets: string[] = r.targets || [];
  const channels = r.kind === "posted"
    ? <span className="small muted">{(r.posts || []).map((x: any) => x.username || x.account_id).join(", ")}</span>
    : targets.length ? <button className="sm ghost" style={{ padding: 0 }} onClick={onChannels}>📺 {targets.map(nameOf).join(", ")} <span className="faint">✎</span></button>
      : r.kind === "review" ? null : <button className="sm" style={{ borderStyle: "dashed", color: "var(--yellow)" }} onClick={onChannels}>⚠ No channel — choose</button>;
  return (
    <div className="it" style={{ alignItems: "flex-start" }}>
      <div className="row" style={{ minWidth: 0, flex: 1, flexWrap: "nowrap", alignItems: "flex-start" }}>
        <input type="checkbox" checked={checked} onChange={(e) => onCheck(e.target.checked)} aria-label="Select" style={{ marginTop: 6 }} />
        <Pic project={r.project} name={r.name} sid={r.sid} thumb={r.thumb} />
        <div style={{ display: "grid", gap: 3, minWidth: 0 }}>
          <b>{r.title || r.label}</b>
          <span className="small muted">{r.label !== r.title ? r.label : ""}{r.duration ? ` · ${mmss(r.duration)}` : ""}{r.privacy ? ` · ${r.privacy}` : ""}{r.missing ? " · the video file is gone" : ""}</span>
          <span className="row small" style={{ gap: 6 }}>
            {r.kind === "review" && <StatusPill s={r.status} />}
            {r.kind === "ready" && (r.qstatus === "posting" ? <Pill tone="warn">uploading…</Pill>
              : r.planned ? <Pill tone="ok">⏰ {r.planned.label}</Pill>
              : <Pill tone="warn">{!sched.enabled ? "waits — auto-posting paused" : targets.length ? `#${i + 1} in line` : "no channel, no time"}</Pill>)}
            {r.kind === "error" && <span style={{ color: "var(--red)" }}>⚠ {r.qerror || r.last_error}</span>}
            {r.kind === "posted" && <Pill tone="ok">✓ posted {when(r.at)}</Pill>}
            {channels}
            {r.kind !== "posted" && r.expires_in_days != null && r.kind !== "ready" && <span className="faint">🗑 deleted in {r.expires_in_days} day(s) unless scheduled</span>}
            {r.superseded && <span style={{ color: "var(--yellow)" }}>the cut changed after approval</span>}
          </span>
          {r.kind === "posted" && (
            <span className="row small">{(r.posts || []).map((x: any) => x.url && (
              <a key={x.account_id} className="btn sm ghost" href={x.url} target="_blank" rel="noreferrer">▶ {x.username || "YouTube"}
                {stats[x.video_id] ? ` · ${stats[x.video_id].views.toLocaleString()} views` : ""}</a>))}</span>
          )}
        </div>
      </div>
      <div className="row" style={{ justifyContent: "flex-end" }}>
        {r.kind === "review" && <>
          <ConfirmButton className="sm primary" confirm="Approve and schedule?" onConfirm={() => bulk("approve", [r], {}, "Approved — it takes the next posting time")}>✓ Approve</ConfirmButton>
          <Link className="btn sm" href={`/chapter/${enc(r.project)}?tab=video`}>▶ Watch</Link>
        </>}
        {r.kind === "ready" && r.qstatus === "queued" && <>
          {i > 0 && <Busy className="sm" title="first in line — takes the next posting time" onClick={() => act(async () => { await api("/api/studio/queue/top", { id: r.qid }); reload(); }, "It posts next")}>⏫ Post next</Busy>}
          <ConfirmButton className="sm" confirm={`Post now (${r.privacy})?`} disabled={r.missing} onConfirm={() => bulk("post_now", [r], {}, "Posting")}>🚀 Now</ConfirmButton>
          <ConfirmButton className="sm ghost" confirm="Back to Needs review?" onConfirm={() => bulk("back", [r], {}, "Back in Needs review")}>↩</ConfirmButton>
          <Busy className="sm ghost" aria-label="Remove" onClick={async () => bulk("remove", [r], {}, "Removed")}>✕</Busy>
        </>}
        {r.kind === "error" && <>
          <ConfirmButton className="sm" confirm="Try again?" onConfirm={() => bulk("post_now", [r], {}, "Trying again")}>↻ Retry</ConfirmButton>
          <ConfirmButton className="sm ghost" confirm="Back to Needs review?" onConfirm={() => bulk("back", [r], {}, "Back in Needs review")}>↩</ConfirmButton>
        </>}
        {r.kind !== "review" && <Link className="btn sm ghost" href={`/chapter/${enc(r.project)}?tab=video`}>✎ Edit</Link>}
      </div>
    </div>
  );
}

function ChannelPicker({ items, accounts, onClose, onSave }: { items: any[]; accounts: any[]; onClose: () => void; onSave: (t: string[]) => void }) {
  const first = items[0]?.targets || [];
  const [t, setT] = useState<string[]>(first);
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
    <img src={src} alt="" loading="lazy" style={{ width: 128, aspectRatio: "16/9", objectFit: "cover", borderRadius: 8, background: "var(--panel2)", flex: "none" }}
      onError={() => { if (!triedCover && sid) { setTriedCover(true); setSrc(`/api/watchlist/cover/${enc(sid)}`); } else setSrc(null); }} />
  ) : <div style={{ width: 128, aspectRatio: "16/9", background: "var(--panel2)", borderRadius: 8, flex: "none" }} />;
}
