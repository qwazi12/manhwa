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
  async function move(id: string, dir: number) {
    const ids = live.map((r: any) => r.qid);
    const i = ids.indexOf(id), k = i + dir;
    if (i < 0 || k < 0 || k >= ids.length) return;
    [ids[i], ids[k]] = [ids[k], ids[i]];
    await act(async () => { await api("/api/studio/queue/reorder", { ids }); reload(); });
  }
  return (
    <>
      <PageHead title="Queue" sub="Watch a video, approve it, and it takes the next free posting slot. Posted videos keep their links." />
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
        ["review", `To review (${toReview.length})`], ["scheduled", `Scheduled (${queued.length})`],
        ["posted", `Posted (${(s?.published || []).length})`], ["errors", `Errors (${failed.length})`]]} />
      {!s ? <Empty>Loading…</Empty> : (
        <Card pad={false}>
          {tab === "review" && (toReview.length === 0 ? <Empty>No videos waiting. When a chapter is rendered, it appears here to watch.</Empty> : (
            <div className="list">{toReview.map((r: any) => (
              <div className="it" key={r.id}>
                <div className="row"><StatusPill s={r.status} /><b>{r.title}</b>{r.status?.superseded && <span className="small" style={{ color: "var(--yellow)" }}>the cut changed after approval</span>}</div>
                <Link className="btn sm primary" href={`/chapter/${r.id}?tab=video`}>Watch and approve →</Link>
              </div>))}</div>))}
          {tab === "scheduled" && (queued.length === 0 ? <Empty>Nothing scheduled. Approve a video after watching it and it lands here.</Empty> : (
            <div className="list">{queued.map((r: any, i: number) => (
              <QRow key={r.qid} r={r}>
                <span className="small muted">{r.qstatus === "posting" ? "uploading…" : i === 0 ? (sched.enabled ? `next · ${sched.next || ""}` : "next") : `#${i + 1}`}</span>
                {r.qstatus === "queued" && <>
                  <ConfirmButton className="sm primary" confirm={`Post now (${r.privacy})?`} disabled={r.missing}
                    onConfirm={() => act(async () => { await api("/api/studio/queue/post", { id: r.qid }); reload(); }, "Posting")}>Post now</ConfirmButton>
                  <button className="sm" disabled={i === 0} onClick={() => move(r.qid, -1)} aria-label="Move up">↑</button>
                  <button className="sm" disabled={i === live.length - 1} onClick={() => move(r.qid, 1)} aria-label="Move down">↓</button>
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
                  <div style={{ display: "grid", gap: 3 }}>
                    <b>{p.title}</b>
                    <span className="small muted">{p.label} · {when(p.at)} · {p.privacy}{p.partial ? " · some channels failed" : ""}</span>
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
              <QRow key={r.qid} r={r}>
                <span className="small" style={{ color: "var(--red)" }}>{r.qerror}</span>
                <ConfirmButton className="sm" confirm="Try again?" onConfirm={() => act(async () => { await api("/api/studio/queue/post", { id: r.qid }); reload(); }, "Posting")}>↻ Try again</ConfirmButton>
                <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/studio/queue/remove", { id: r.qid }); reload(); }, "Removed")}>Remove</Busy>
              </QRow>))}</div>))}
        </Card>
      )}
    </>
  );
}

function QRow({ r, children }: { r: any; children: React.ReactNode }) {
  return (
    <div className="it">
      <div className="row" style={{ minWidth: 0, flex: 1 }}>
        {r.thumb ? <img src={r.thumb} alt="" style={{ width: 96, aspectRatio: "16/9", objectFit: "cover", borderRadius: 6 }} /> :
          <div style={{ width: 96, aspectRatio: "16/9", background: "var(--panel2)", borderRadius: 6 }} />}
        <div style={{ display: "grid", gap: 2, minWidth: 0 }}>
          <b>{r.title || r.label}</b>
          <span className="small muted">{r.label} · {mmss(r.duration)} · {r.privacy} → {(r.targets || []).join(", ") || "no channel"}{r.missing ? " · the video file is gone" : ""}</span>
        </div>
      </div>
      <div className="row">{children}<Link className="btn sm ghost" href={`/chapter/${enc(r.project)}?tab=video`}>Details</Link></div>
    </div>
  );
}
