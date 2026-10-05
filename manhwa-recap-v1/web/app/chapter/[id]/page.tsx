"use client";
import Link from "next/link";
import { useParams } from "next/navigation";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { when } from "@/lib/fmt";
import { LAST_CHAPTER_KEY } from "@/components/Shell";
import { Busy, ConfirmButton, Empty, PageHead, StatusPill, Tabs, useAct } from "@/components/ui";
import Board from "@/components/chapter/Board";
import Issues from "@/components/chapter/Issues";
import VideoTab from "@/components/chapter/VideoTab";

type T = "board" | "issues" | "video";
const STEP_N: Record<string, string> = { scrape: "1", split: "2", describe: "3", narrate: "4", voice: "5", match: "6", render: "7", export: "8" };

export default function Chapter() {
  const { id } = useParams<{ id: string }>();
  const { data: c, reload, error } = useApi<any>(`/api/chapter/${encodeURIComponent(id)}`, 10000);
  const [tab, setTab] = useState<T>("board");
  const [boardKey, setBoardKey] = useState(0);
  const act = useAct();
  useEffect(() => {
    try { const t = new URLSearchParams(location.search).get("tab") as T; if (t) setTab(t); } catch {}
  }, []);
  useEffect(() => {
    if (c?.title) try { localStorage.setItem(LAST_CHAPTER_KEY, JSON.stringify({ id, title: c.title })); } catch {}
  }, [c?.title, id]);
  if (error) return <><PageHead title="Chapter" /><div className="banner bad">{error}</div></>;
  if (!c) return <Empty>Loading…</Empty>;
  const k = c.status?.key;
  const canRender = c.steps?.find((s: any) => s.key === "match")?.state === "done";
  const render = () => act(async () => {
    await api("/api/activate", { id });
    await api("/api/storyboard/approve", { approved: true });
    reload();
  }, "Rendering — the video appears under Video when it’s done");
  return (
    <>
      <PageHead
        title={c.title}
        sub={<span className="row"><StatusPill s={c.status} /><span>{c.status?.hint}</span>{c.status?.reason && <span style={{ color: "var(--red)" }}>{String(c.status.reason).slice(0, 200)}</span>}</span>}
        right={<>
          {c.url && <a className="btn sm ghost" href={c.url} target="_blank" rel="noreferrer">Source ↗</a>}
          {canRender && !["rendering", "making"].includes(k) && (
            <ConfirmButton className={k === "to_review" ? "primary" : ""} confirm="Render the video now?"
              title="renders the ticked segments and exports the final video (re-records the voice first if the studio voice changed)"
              onConfirm={render}>{k === "to_review" ? "Render video" : "Render again"}</ConfirmButton>
          )}
        </>}
      />
      <div className="steps" aria-label="Pipeline steps">
        {(c.steps || []).map((s: any) => (
          <div key={s.key} className={`step ${s.state}`}>
            <span className="n">{STEP_N[s.key]} · {s.state === "done" ? "done" : s.state === "partial" ? "part done" : "not yet"}</span>
            <b>{s.label}</b>
            <span className="d">{s.detail}</span>
            {s.at && <span className="d faint">{when(s.at)}</span>}
            {s.rerun && c.can_rerun && !c.busy && (
              <ConfirmButton className="sm ghost" confirm={`Redo from here? ${s.rerun.cost}`} title={`${s.rerun.label} — clears this step and everything after it (board edits included), then makes the chapter again from here`}
                onConfirm={() => act(async () => { await api("/api/pipeline/rerun", { project: id, step: s.key }); reload(); }, "Re-running — watch the jobs bar")}>↻ Redo</ConfirmButton>
            )}
          </div>
        ))}
      </div>
      {c.busy && <div className="banner info">{c.busy}. The page updates by itself.</div>}
      <Tabs<T> value={tab} onChange={setTab} tabs={[["board", "Board"], ["issues", "Issues"], ["video", c.video ? "Video & publish" : "Video & publish (not rendered)"]]} />
      {tab === "board" && <Board id={id} key={boardKey} onChange={() => { reload(); }} />}
      {tab === "issues" && <Issues id={id} onFixed={() => { setBoardKey((n) => n + 1); reload(); }} />}
      {tab === "video" && (c.video ? <VideoTab id={id} name={c.video} status={c.status} onChange={reload} /> :
        <Empty>No video yet. Check the board, then press Render video.{" "}
          <Busy className="sm" onClick={() => Promise.resolve(setTab("board"))}>Go to the board</Busy></Empty>)}
      <p className="small faint">Prefer the previous layout? <Link href="/storyboard">Classic board</Link> (opens the active chapter).</p>
    </>
  );
}
