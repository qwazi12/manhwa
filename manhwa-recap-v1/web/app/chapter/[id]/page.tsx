"use client";
import Link from "next/link";
import { useParams, useRouter } from "next/navigation";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { money, when } from "@/lib/fmt";
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

// Owner, 2026-10-05: the board showed Murim ch.44 while "Watch the video" showed
// Iron-Blooded ch.180. Switching chapters reused this page with the previous
// chapter's state (the chosen video version, tab, sheet). Keying by the chapter
// id gives every chapter a fresh page.
export default function Chapter() {
  const { id } = useParams<{ id: string }>();
  return <ChapterPage key={id} id={id} />;
}

function ChapterPage({ id }: { id: string }) {
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
  const inQueue = c?.render_queue?.status === "waiting" ? c.render_queue : null;
  const fixing = (c?.videos || []).some((v: any) => v.phone_fix);
  // poll fast while a render runs or waits in the queue; when it finishes, open the new video
  useEffect(() => {
    if (!rendering && !inQueue && !fixing) return;
    const t = setInterval(reload, 3000);
    return () => clearInterval(t);
  }, [rendering, !!inQueue, fixing, reload]);
  // Toast when a render ends while this page is open — compared by job id and
  // status, so a render that fails within a second (before any poll saw it
  // running) still says so (owner, 2026-10-05). The first load sets the baseline.
  useEffect(() => {
    if (!c) return;
    const was = prevJob.current;
    const now = rj ? `${rj.id}:${rj.status}` : "none";
    if (was !== null && was !== now && rj) {
      if (rj.status === "done") {
        toast("The video is ready — watch it and approve");
        setVideo(null); setTab("video"); list.reload();
      }
      if (rj.status === "error") toast(`Render failed: ${rj.error || "see Activity"}`, true);
    }
    prevJob.current = now;
  }, [c, rj?.id, rj?.status]);

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
  // Every render goes through the render queue: it starts straight away when
  // nothing else is rendering, else waits its turn (one chapter at a time).
  async function render(keepVoice = true) {
    setSheet(false);
    await act(async () => {
      const r = await api("/api/render-queue/add", { projects: [id], keep_voice: keepVoice });
      if (!r.added.length) throw new Error(`Not queued: ${r.skipped[0]?.reason || "unknown reason"}`);
      reload();
      setTimeout(reload, 1500);   // a fast start or failure shows without waiting for the next poll
    }, "Added to the render queue — it starts as soon as nothing else is rendering");
  }
  const rv = c.revoice;
  const failed = rj?.status === "error" && !(c.videos?.[0]?.at && rj.ts && c.videos[0].at > rj.ts) ? rj : null;

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
          ) : inQueue ? (
            <span className="pill warn" style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 600 }}>
              Queued (#{inQueue.place})
              <button className="sm ghost" title="Take out of queue" style={{ padding: "0 4px", fontSize: 11, lineHeight: 1 }}
                onClick={() => act(async () => { await api("/api/render-queue/remove", { id: inQueue.item }); reload(); }, "Removed from queue")}>✕</button>
            </span>
          ) : canRender && !["making"].includes(k) && (
            <button className={k === "to_review" ? "primary" : ""} onClick={() => setSheet(!sheet)}>{shown ? "Render again" : "Render video"}</button>
          )}
        </>}
      />
      {sheet && !rendering && (
        <Card>
          <p><b>Render {c.title}?</b> It makes every clip that’s ticked “in video”, then exports the final video (sped up and levelled for YouTube) and copies it to Google Drive. Usually 3–6 minutes; this page shows the progress.</p>
          {rv ? (
            <>
              <div className={`banner ${rv.fits ? "warn" : "bad"}`}>
                The studio voice changed since this chapter was voiced. It’s in <b>{rv.from}</b>; the studio voice is now <b>{rv.to}</b>.
                Using the new voice re-records all {rv.lines} lines first: about <b>{money(rv.est_usd)}</b>.
                {" "}Today: {money(rv.spent_today)} of {money(rv.cap)} spent.
                {!rv.fits && <> That’s more than what’s left today, so the new voice can’t be used until after midnight ET.</>}
              </div>
              <div className="row">
                <button className="primary" onClick={() => render(true)}>Render in this chapter’s voice (free)</button>
                <button disabled={!rv.fits} onClick={() => render(false)}>Re-record in the new voice (~{money(rv.est_usd)})</button>
                <button className="ghost" onClick={() => setSheet(false)}>Cancel</button>
              </div>
            </>
          ) : (
            <>
              <p className="small muted">Free: it runs on the server and the voice is already recorded.</p>
              <div className="row"><button className="primary" onClick={() => render()}>Render now</button><button className="ghost" onClick={() => setSheet(false)}>Cancel</button></div>
            </>
          )}
        </Card>
      )}
      {failed && !rendering && !sheet && (
        <div className="banner bad">
          <div><b>The last render failed</b> {when(failed.ended || failed.ts)}{failed.stage ? ` (while ${STAGE_WORD[failed.stage] || failed.stage})` : ""}: {failed.error || "no reason recorded — see Activity"}</div>
          <div className="row" style={{ marginTop: 8 }}>
            {canRender && <button className="sm primary" onClick={() => setSheet(true)}>Try again…</button>}
            <Link className="btn sm ghost" href="/activity">Open Activity</Link>
          </div>
        </div>
      )}
      {rendering && <RenderProgress rj={rj} />}
      {inQueue && !rendering && (
        <div className="banner info spread">
          <span><b>Waiting in the render queue</b> — {inQueue.place === 1 ? "it’s next" : `#${inQueue.place} in line`}. Chapters render one by one; it will start automatically once the previous render finishes.</span>
          <Busy className="sm" onClick={() => act(async () => { await api("/api/render-queue/remove", { id: inQueue.item }); reload(); }, "Taken out of the queue")}>Take out</Busy>
        </div>
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
      {c.ingest && (
        <Card>
          <div className="spread">
            <span><b>{["queued", "running"].includes(c.ingest.status) ? "Being made" : c.ingest.status === "error" ? "Stopped with an error" : "Waiting"}</b>
              {" · "}{c.ingest.stage || c.ingest.status}{c.ingest.msg ? ` — ${c.ingest.msg}` : ""}{c.ingest.error ? ` — ${c.ingest.error}` : ""}</span>
            <span className="row">
              {["queued", "running"].includes(c.ingest.status) && (
                <ConfirmButton className="sm danger" confirm="Stop making it?" onConfirm={() => act(async () => { await api("/api/jobs/control", { job_id: c.ingest.job, action: "stop" }); reload(); }, "Stopping")}>■ Stop</ConfirmButton>)}
              {["paused", "pausing"].includes(c.ingest.status) && <Busy className="sm" onClick={() => act(async () => { await api("/api/jobs/control", { job_id: c.ingest.job, action: "resume" }); reload(); }, "Resumed")}>▶ Resume</Busy>}
              {["budget_paused", "interrupted", "error"].includes(c.ingest.status) && <Busy className="sm" onClick={() => act(async () => { await api("/api/jobs/resume", { job_id: c.ingest.job }); reload(); }, "Resumed")}>▶ Resume</Busy>}
            </span>
          </div>
          {["queued", "running"].includes(c.ingest.status) && <div className="bar"><i style={{ width: `${c.ingest.pct || 3}%` }} /></div>}
        </Card>
      )}
      {c.busy && !rendering && !c.ingest && <div className="banner info">{c.busy}. The page updates by itself.</div>}
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
          <Card><p><b>Rendering…</b> The video opens here when it’s done — the progress is shown above.</p></Card>
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
            {(() => {
              const v = (c.videos || []).find((x: any) => x.name === shown);
              if (!v || v.phone_ok !== false) return null;
              return v.phone_fix ? (
                <div className="banner info"><b>Fixing it for phones…</b> {v.phone_fix.pct ?? 0}%. It plays here when done.
                  <div className="bar" style={{ marginTop: 6 }}><i style={{ width: `${v.phone_fix.pct || 3}%` }} /></div></div>
              ) : (
                <div className="banner warn spread">
                  <span><b>This video won’t play on a phone.</b> It was made before the phone fix (it plays on a computer and on YouTube). Fixing it re-encodes the same video: free, a few minutes, and the Drive copy is replaced.</span>
                  <Busy className="sm primary" onClick={() => act(async () => { await api("/api/exports/phonefix", { project: id, name: shown }); reload(); }, "Fixing it for phones — progress shows here")}>Fix for phones</Busy>
                </div>
              );
            })()}
            <VideoTab key={`${id}:${shown}:${(c.videos || []).find((x: any) => x.name === shown)?.size_mb}`} id={id} name={shown} status={c.status} onChange={() => { reload(); list.reload(); }} />
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

const STAGE_WORD: Record<string, string> = { queued: "waiting to start", revoice: "re-recording the voice", render: "rendering clips", export: "exporting" };

/** Scrapper-style progress: every stage of the render, the current one with its count. */
function RenderProgress({ rj }: { rj: any }) {
  const st = rj.stage || "queued";
  const voiced = st === "revoice" || /^re-recording/.test(rj.note || "");
  const order = [...(voiced ? ["revoice"] : []), "render", "export", "drive"];
  const at = order.indexOf(st === "queued" ? order[0] : st);
  const rows: [string, string][] = [
    ...(voiced ? [["revoice", "Re-record the voice (paid)"] as [string, string]] : []),
    ["render", "Render the clips (free)"], ["export", "Export the video (free)"], ["drive", "Copy to Google Drive"],
  ];
  const pct = rj.total ? Math.round(100 * (rj.done || 0) / rj.total) : null;
  const secs = rj.ts ? Math.max(0, Math.round(Date.now() / 1000 - rj.ts)) : 0;
  return (
    <Card title={<span>🎬 Rendering · {secs >= 60 ? `${Math.floor(secs / 60)} min ${secs % 60} s` : `${secs} s`}{rj.status !== "running" ? ` · ${rj.status}` : ""}</span>}>
      <div style={{ display: "grid", gap: 8 }}>
        {rows.map(([key, label], i) => {
          const state = at < 0 ? "todo" : i < at ? "done" : i === at ? "now" : "todo";
          return (
            <div key={key} style={{ display: "grid", gap: 4 }}>
              <div className="spread small">
                <span>{state === "done" ? "✓" : state === "now" ? "▶" : "○"} <b style={{ opacity: state === "todo" ? 0.6 : 1 }}>{label}</b></span>
                <span className="muted">{state === "now" ? (st === "queued" ? "starting…" : rj.total && key !== "export" ? `${rj.done || 0} of ${rj.total}` : "working…") : state === "done" ? "done" : key === "drive" ? "after the export" : ""}</span>
              </div>
              {state === "now" && <div className="bar"><i style={{ width: `${pct ?? 8}%` }} /></div>}
            </div>
          );
        })}
        {rj.note && voiced && <span className="small muted">{rj.note}</span>}
      </div>
    </Card>
  );
}
