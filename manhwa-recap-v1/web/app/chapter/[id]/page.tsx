"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { when } from "@/lib/fmt";
import { LAST_CHAPTER_KEY } from "@/components/Shell";
import { Busy, Card, ConfirmButton, Empty, PageHead, StatusPill, Tabs, useAct, useToast } from "@/components/ui";
import Board from "@/components/chapter/Board";
import Issues from "@/components/chapter/Issues";
import VideoTab from "@/components/chapter/VideoTab";

type T = "board" | "issues" | "video";
const STEP_N: Record<string, string> = { scrape: "1", split: "2", describe: "3", narrate: "4", voice: "5", match: "6", render: "7", export: "8" };
// the review order used by Previous / Next (owner, 2026-10-04: changing chapters had too much friction)
const REVIEW_ORDER = ["video_ready", "to_review", "failed", "waiting"];
const GROUPS: [string, string[]][] = [
  ["Watch the video", ["video_ready"]], ["Check the board", ["to_review"]], ["Needs attention", ["failed", "waiting"]],
  ["Being made", ["making", "rendering"]], ["Scheduled & posted", ["scheduled", "posting", "posted"]], ["Archived", ["archived"]],
];

export default function Chapter() {
  const { id } = useParams<{ id: string }>();
  const router = useRouter();
  const { data: c, reload, error } = useApi<any>(`/api/chapter/${encodeURIComponent(id)}`, 10000);
  const list = useApi<any>("/api/chapters", 30000);
  const [tab, setTab] = useState<T>("board");
  const [boardKey, setBoardKey] = useState(0);
  const [sheet, setSheet] = useState(false);
  const [video, setVideo] = useState<string | null>(null);
  const act = useAct();
  const toast = useToast();
  const prevJob = useRef<any>(null);

  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as T; if (t) setTab(t === ("publish" as any) ? "video" : t); } catch {}
  }, [id]);
  useEffect(() => {
    if (c?.title) try { localStorage.setItem(LAST_CHAPTER_KEY, JSON.stringify({ id, title: c.title })); } catch {}
  }, [c?.title, id]);
  const rj = c?.render_job;
  const rendering = rj && ["queued", "running", "paused", "pausing"].includes(rj.status);
  // poll fast while a render runs; when it finishes, open the new video
  useEffect(() => {
    if (!rendering) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [rendering, reload]);
  useEffect(() => {
    const was = prevJob.current;
    if (was && ["queued", "running"].includes(was.status) && rj && rj.status === "done") {
      toast("The video is ready — watch it and approve");
      setVideo(null); setTab("video"); list.reload();
    }
    if (was && ["queued", "running"].includes(was.status) && rj && rj.status === "error") toast(`Render failed: ${rj.error || "see Activity"}`, true);
    prevJob.current = rj;
  }, [rj?.status]);

  const chapters: any[] = list.data?.chapters || [];
  const queue = useMemo(() => chapters
    .filter((r) => REVIEW_ORDER.includes(r.status.key))
    .sort((a, b) => REVIEW_ORDER.indexOf(a.status.key) - REVIEW_ORDER.indexOf(b.status.key) || (b.updated || 0) - (a.updated || 0)), [chapters]);
  const qi = queue.findIndex((r) => r.id === id);
  const prev = qi > 0 ? queue[qi - 1] : null;
  const next = qi >= 0 ? queue[qi + 1] : queue.find((r) => r.id !== id);

  if (error) return <><PageHead title="Board" /><div className="banner bad">{error}</div></>;
  if (!c) return <Empty>Loading…</Empty>;
  const k = c.status?.key;
  const canRender = c.steps?.find((s: any) => s.key === "match")?.state === "done";
  const shown = video || c.video;
  const steps = (c.steps || []).map((s: any) => {
    if (!rendering) return s;
    if (s.key === "render") return { ...s, state: "partial", detail: rj.total ? `clip ${rj.done || 0} of ${rj.total}` : (rj.stage || "starting") };
    if (s.key === "export") return { ...s, state: "missing", detail: rj.stage && /export/i.test(rj.stage) ? "exporting…" : "after the clips" };
    return s;
  });
  async function render() {
    setSheet(false);
    await act(async () => {
      await api("/api/activate", { id });
      await api("/api/storyboard/approve", { approved: true });
      reload();
    }, "Rendering — progress shows in steps 7 and 8");
  }

  return (
    <>
      <div className="spread">
        <div className="row" style={{ flex: 1, minWidth: 0 }}>
          <select aria-label="Choose a chapter" value={id} style={{ maxWidth: 420, fontWeight: 600 }}
            onChange={(e) => router.push(`/chapter/${encodeURIComponent(e.target.value)}`)}>
            {!chapters.some((r) => r.id === id) && <option value={id}>{c.title}</option>}
            {GROUPS.map(([label, keys]) => {
              const items = chapters.filter((r) => keys.includes(r.status.key));
              return items.length ? (
                <optgroup key={label} label={`${label} (${items.length})`}>
                  {items.map((r) => <option key={r.id} value={r.id}>{r.title}</option>)}
                </optgroup>
              ) : null;
            })}
          </select>
          <button className="sm" disabled={!prev} onClick={() => prev && router.push(`/chapter/${prev.id}`)}>‹ Previous</button>
          <button className="sm" disabled={!next} onClick={() => next && router.push(`/chapter/${next.id}${next.status.key === "video_ready" ? "?tab=video" : ""}`)}>
            Next to check ›</button>
          {queue.length > 0 && <span className="small muted">{qi >= 0 ? `${qi + 1} of ${queue.length}` : `${queue.length}`} waiting for you</span>}
        </div>
      </div>
      <PageHead
        title={c.title}
        sub={<span className="row"><StatusPill s={c.status} /><span>{c.status?.hint}</span>{c.status?.reason && <span style={{ color: "var(--red)" }}>{String(c.status.reason).slice(0, 200)}</span>}</span>}
        right={<>
          {c.url && <a className="btn sm ghost" href={c.url} target="_blank" rel="noreferrer">Source ↗</a>}
          {c.archive && !c.archive.keep && <Busy className="sm" onClick={() => act(async () => { await api("/api/projects/archive", { id, action: "keep" }); reload(); }, "Kept — it won’t be deleted")}>Keep</Busy>}
          {c.archive && <Busy className="sm" onClick={() => act(async () => { await api("/api/projects/archive", { id, action: "unarchive" }); reload(); }, "Back in the library")}>Unarchive</Busy>}
          <ConfirmButton className="sm danger" confirm="Delete this chapter? This can’t be undone"
            onConfirm={() => act(async () => {
              await api("/api/projects/delete", { id });
              try { localStorage.removeItem(LAST_CHAPTER_KEY); } catch {}
              router.push("/library");
            }, "Chapter deleted")}>🗑 Delete</ConfirmButton>
          {rendering ? (
            <ConfirmButton className="sm danger" confirm="Stop the render?" onConfirm={() => act(async () => { await api("/api/jobs/control", { job_id: rj.id, action: "stop" }); reload(); }, "Stopping")}>■ Stop render</ConfirmButton>
          ) : canRender && !["making"].includes(k) && (
            <button className={k === "to_review" ? "primary" : ""} onClick={() => setSheet(!sheet)}>{shown ? "Render again" : "Render video"}</button>
          )}
        </>}
      />
      {sheet && !rendering && (
        <Card>
          <p><b>Render {c.title}?</b> It makes every clip that’s ticked “in video”, then exports the final video (sped up and levelled for YouTube) and copies it to Google Drive. Usually 3–6 minutes; this page shows the progress.</p>
          <p className="small muted">Free (it runs on the server), except when the studio narrator changed since this chapter was voiced: then the lines are re-recorded first (about $0.14).</p>
          <div className="row"><button className="primary" onClick={render}>Render now</button><button className="ghost" onClick={() => setSheet(false)}>Cancel</button></div>
        </Card>
      )}
      <div className="steps" aria-label="Pipeline steps">
        {steps.map((s: any) => (
          <div key={s.key} className={`step ${s.state}`}>
            <span className="n">{STEP_N[s.key]} · {s.state === "done" ? "done" : s.state === "partial" ? (rendering && ["render", "export"].includes(s.key) ? "working" : "part done") : "not yet"}</span>
            <b>{s.label}</b>
            <span className="d">{s.detail}</span>
            {s.at && !(rendering && ["render", "export"].includes(s.key)) && <span className="d faint">{when(s.at)}</span>}
            {s.key === "export" && shown && !rendering && <button className="sm" onClick={() => setTab("video")}>▶ Watch</button>}
            {s.rerun && c.can_rerun && !c.busy && (
              <ConfirmButton className="sm ghost" confirm={`Redo from here? ${s.rerun.cost}`} title={`${s.rerun.label} — clears this step and everything after it (board edits included), then makes the chapter again from here`}
                onConfirm={() => act(async () => { await api("/api/pipeline/rerun", { project: id, step: s.key }); reload(); }, "Re-running — watch the jobs bar")}>↻ Redo</ConfirmButton>
            )}
          </div>
        ))}
      </div>
      {c.busy && !rendering && <div className="banner info">{c.busy}. The page updates by itself.</div>}
      {shown && c.drive_ready && (
        <div className="row small">
          {c.drive?.video === c.video ? (
            <><span className="pill t-ok">✓ In Google Drive</span><a href={c.drive.folder_link} target="_blank" rel="noreferrer">Open the folder ↗</a><span className="muted">copied {when(c.drive.at)}</span></>
          ) : (
            <><span className="pill t-muted">Not in Google Drive yet</span>
              <Busy className="sm" onClick={() => act(async () => { await api("/api/drive/copy", { project: id }); }, "Copying to Drive — it shows here when done")}>Copy now</Busy></>
          )}
        </div>
      )}
      <Tabs<T> value={tab} onChange={setTab} tabs={[["board", "Check the board"], ["issues", "Issues"], ["video", rendering ? "Watch the video (rendering…)" : shown ? "Watch the video" : "Watch the video (not rendered yet)"]]} />
      {tab === "board" && <Board id={id} key={boardKey} onChange={() => { reload(); }} sentBack={c.sent_back} builtWith={c.built_with} />}
      {tab === "issues" && <Issues id={id} onFixed={() => { setBoardKey((n) => n + 1); reload(); }} />}
      {tab === "video" && (
        rendering ? (
          <Card><p><b>Rendering…</b> {rj.total ? `clip ${rj.done || 0} of ${rj.total}` : rj.stage || "starting"}. The video opens here when it’s done.</p>
            <div className="bar"><i style={{ width: `${rj.total ? Math.round(100 * (rj.done || 0) / rj.total) : 5}%` }} /></div></Card>
        ) : shown ? (
          <>
            {(c.videos || []).length > 1 && (
              <div className="row small">
                <span className="muted">Version:</span>
                <select value={shown} onChange={(e) => setVideo(e.target.value)} style={{ width: "auto" }} aria-label="Video version">
                  {c.videos.map((v: any) => <option key={v.name} value={v.name}>{when(v.at)} · {v.size_mb} MB{v.review === "approved" ? " · approved" : ""}{v.name === c.video ? " · newest" : ""}</option>)}
                </select>
                {(() => { const v = c.videos.find((x: any) => x.name === shown); return v ? <span className="muted">deleted in {v.expires_in_days} day(s) unless scheduled</span> : null; })()}
                {shown !== c.video && (
                  <ConfirmButton className="sm danger" confirm="Delete this version?" onConfirm={() => act(async () => { await api("/api/exports/delete", { project: id, name: shown }); setVideo(null); reload(); }, "Version deleted")}>Delete this version</ConfirmButton>
                )}
              </div>
            )}
            <VideoTab key={shown} id={id} name={shown} status={c.status} onChange={() => { reload(); list.reload(); }} />
          </>
        ) : (
          <>
            <Card>
              <div className="spread"><span><b>No video yet.</b> Check the board, then render it. You can already prepare the title, description and tags below.</span>
                {canRender && <button className="primary" onClick={() => setSheet(true)}>Render video</button>}</div>
            </Card>
            <VideoTab key="_draft" id={id} name="_draft" status={c.status} draft onChange={reload} />
          </>
        )
      )}
      {next && next.id !== id && (k === "scheduled" || rendering) && (
        <div className="banner info row"><span>Next waiting for you: <b>{next.title}</b></span>
          <Link className="btn sm" href={`/chapter/${next.id}${next.status.key === "video_ready" ? "?tab=video" : ""}`}>Open ›</Link></div>
      )}
    </>
  );
}
