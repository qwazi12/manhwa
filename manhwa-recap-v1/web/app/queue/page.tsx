"use client";
import Link from "next/link";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, mmss, when } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, PageHead, Pill, StatusPill, useAct } from "@/components/ui";

type Tab = "review" | "scheduled" | "posted" | "errors";

export default function Queue() {
  const studio = useApi<any>("/api/studio", 15000);
  const ready = useApi<any>("/api/chapters?status=video_ready", 15000);
  const all = useApi<any>("/api/chapters", 60000);
  const sidOf: Record<string, string> = {};
  for (const r of all.data?.chapters || []) if (r.series_id) sidOf[r.id] = r.series_id;
  const [tab, setTab] = useState<Tab>("review");
  const [stats, setStats] = useState<Record<string, any>>({});
  const act = useAct();
  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as Tab; if (t) setTab(t); } catch {}
  }, []);
  const s = studio.data;
  const queued = (s?.queue || []).filter((r: any) => r.qstatus !== "failed");
  const failed = (s?.queue || []).filter((r: any) => r.qstatus === "failed");
  const toReview = ready.data?.chapters || [];
  const sched = s?.schedule || {};
  const reload = () => { studio.reload(); ready.reload(); };
  const live = queued.filter((r: any) => r.qstatus === "queued");
  // Owner, 2026-10-05: send a scheduled or failed video back to Home → Needs you
  const back = (r: any) => act(async () => { await api("/api/studio/queue/back", { project: r.project, name: r.name }); reload(); },
    "Back in Needs you — approve it again to re-schedule");
  async function move(id: string, dir: number) {
    const ids = live.map((r: any) => r.qid);
    const i = ids.indexOf(id), k = i + dir;
    if (i < 0 || k < 0 || k >= ids.length) return;
    [ids[i], ids[k]] = [ids[k], ids[i]];
    await act(async () => { await api("/api/studio/queue/reorder", { ids }); reload(); });
  }
  return (
    <>
      <PageHead title="Posting schedule" sub="Watch a video, approve it, and it takes the next free posting time. Posted videos keep their links." />
      <Card>
        <div className="spread">
          <div className="row">
            <Pill tone={sched.enabled ? "ok" : "muted"}>{sched.enabled ? "● Posting schedule on" : "Posting schedule off"}</Pill>
            <span className="small muted">
              {sched.enabled ? `Posts at ${(sched.times || []).join(", ")} ET · next ${sched.next || "—"} · at most ${sched.per_channel_per_day} per channel a day`
                : "Videos wait here until you press Post now."}
            </span>
          </div>
          <Link className="btn sm" href="/settings#schedule">Change schedule</Link>
        </div>
      </Card>
      <Chips<Tab> value={tab} onChange={setTab} items={[
        ["review", `To watch (${toReview.length})`], ["scheduled", `Scheduled (${queued.length})`],
        ["posted", `Posted (${(s?.published || []).length})`], ["errors", `Errors (${failed.length})`]]} />
      {!s ? <Empty>Loading…</Empty> : (
        <Card pad={false}>
          {tab === "review" && (toReview.length === 0 ? <Empty>No videos waiting. When a chapter is rendered, it appears here to watch.</Empty> : (
            <div className="list">
              <div className="it small muted">These are already on Home → Needs you. Approve one and it moves to Scheduled; “↩ Back to review” there brings it back here.</div>{toReview.map((r: any) => (
              <div className="it" key={r.id}>
                <div className="row" style={{ minWidth: 0, flex: 1, flexWrap: "nowrap" }}>
                  <Pic project={r.id} name={r.video} sid={r.series_id} />
                  <div style={{ display: "grid", gap: 4, minWidth: 0 }}>
                    <b>{r.title}</b>
                    <span className="row"><StatusPill s={r.status} />{r.status?.superseded && <span className="small" style={{ color: "var(--yellow)" }}>the cut changed after approval</span>}</span>
                  </div>
                </div>
                <Link className="btn sm primary" href={`/chapter/${r.id}?tab=video`}>Watch the video →</Link>
              </div>))}</div>))}
          {tab === "scheduled" && (queued.length === 0 ? <Empty>Nothing scheduled. Approve a video after watching it and it lands here.</Empty> : (
            <div className="list">{queued.map((r: any, i: number) => (
              <QRow key={r.qid} r={r} sid={sidOf[r.project]}>
                <span className="small muted">{r.qstatus === "posting" ? "uploading…" : i === 0 ? (sched.enabled ? `next · ${sched.next || ""}` : "next") : `#${i + 1}`}</span>
                {r.qstatus === "queued" && <>
                  <ConfirmButton className="sm primary" confirm={`Post now (${r.privacy})?`} disabled={r.missing}
                    onConfirm={() => act(async () => { await api("/api/studio/queue/post", { id: r.qid }); reload(); }, "Posting")}>Post now</ConfirmButton>
                  <button className="sm" disabled={i === 0} onClick={() => move(r.qid, -1)} aria-label="Move up">↑</button>
                  <button className="sm" disabled={i === live.length - 1} onClick={() => move(r.qid, 1)} aria-label="Move down">↓</button>
                  <ConfirmButton className="sm" confirm="Take it off the queue and back to Needs you?" title="off the posting queue, back to Home → Needs you to watch again" onConfirm={() => back(r)}>↩ Back to review</ConfirmButton>
                  <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/studio/queue/remove", { id: r.qid }); reload(); }, "Taken off the queue")}>Remove</Busy>
                </>}
              </QRow>))}</div>))}
          {tab === "posted" && ((s.published || []).length === 0 ? <Empty>Nothing posted yet.</Empty> : (
            <>
              {s.yt_stats && <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
                <Busy className="sm" onClick={() => act(async () => {
                  const ids = s.published.flatMap((p: any) => p.posts.map((x: any) => x.video_id)).filter(Boolean);
                  const r = await api("/api/studio/stats", { video_ids: ids }); setStats(r.stats || {});
                })}>Load YouTube views</Busy></div>}
              <div className="list">{s.published.map((p: any) => (
                <div className="it" key={p.project + p.name}>
                  <div className="row" style={{ minWidth: 0, flex: 1, flexWrap: "nowrap" }}>
                  <Pic project={p.project} name={p.name} sid={sidOf[p.project]} />
                  <div style={{ display: "grid", gap: 3 }}>
                    <b>{p.title}</b>
                    <span className="small muted">{p.label} · {when(p.at)} · {p.privacy}{p.partial ? " · some channels failed" : ""}</span>
                  </div>
                  </div>
                  <div className="row">{p.posts.map((x: any) => (
                    <span key={x.account_id} className="row">
                      {x.url ? <a className="btn sm" href={x.url} target="_blank" rel="noreferrer">▶ {x.username || x.account_id}</a> : <span className="small">{x.username}</span>}
                      {stats[x.video_id] && <span className="small muted">{stats[x.video_id].views.toLocaleString()} views · {stats[x.video_id].likes.toLocaleString()} likes</span>}
                    </span>))}</div>
                </div>))}</div>
            </>))}
          {tab === "errors" && (failed.length === 0 ? <Empty>No failed posts.</Empty> : (
            <div className="list">{failed.map((r: any) => (
              <QRow key={r.qid} r={r} sid={sidOf[r.project]}>
                <span className="small" style={{ color: "var(--red)" }}>{r.qerror}</span>
                <ConfirmButton className="sm" confirm="Try again?" onConfirm={() => act(async () => { await api("/api/studio/queue/post", { id: r.qid }); reload(); }, "Posting")}>↻ Try again</ConfirmButton>
                <ConfirmButton className="sm" confirm="Back to Needs you for another look?" onConfirm={() => back(r)}>↩ Back to review</ConfirmButton>
                <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/studio/queue/remove", { id: r.qid }); reload(); }, "Removed")}>Remove</Busy>
              </QRow>))}</div>))}
        </Card>
      )}
    </>
  );
}

/** The video's own thumbnail, else the series cover (owner, 2026-10-06:
 *  image cards on the posting schedule). */
function Pic({ project, name, sid, thumb }: { project: string; name?: string | null; sid?: string | null; thumb?: string | null }) {
  const [src, setSrc] = useState<string | null>(thumb || (name ? `/thumbnail?project=${enc(project)}&name=${enc(name)}` : null) || (sid ? `/api/watchlist/cover/${enc(sid)}` : null));
  const [triedCover, setTriedCover] = useState(!name && !thumb);
  return src ? (
    <img src={src} alt="" loading="lazy" style={{ width: 128, aspectRatio: "16/9", objectFit: "cover", borderRadius: 8, background: "var(--panel2)", flex: "none" }}
      onError={() => { if (!triedCover && sid) { setTriedCover(true); setSrc(`/api/watchlist/cover/${enc(sid)}`); } else setSrc(null); }} />
  ) : <div style={{ width: 128, aspectRatio: "16/9", background: "var(--panel2)", borderRadius: 8, flex: "none" }} />;
}

function QRow({ r, sid, children }: { r: any; sid?: string; children: React.ReactNode }) {
  return (
    <div className="it">
      <div className="row" style={{ minWidth: 0, flex: 1, flexWrap: "nowrap" }}>
        <Pic project={r.project} name={r.name} sid={sid} thumb={r.thumb} />
        <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
          <b>{r.title || r.label}</b>
          <span className="small muted">{r.label} · {mmss(r.duration)} · {r.privacy} → {(r.targets || []).join(", ") || "no channel"}{r.missing ? " · the video file is gone" : ""}</span>
        </div>
      </div>
      <div className="row">{children}<Link className="btn sm ghost" href={`/chapter/${enc(r.project)}?tab=video`}>Details</Link></div>
    </div>
  );
}
