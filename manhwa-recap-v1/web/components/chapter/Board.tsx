"use client";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { mmss } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, Pill, useAct } from "@/components/ui";

type F = "video" | "flagged" | "out" | "all";
const PLACE: Record<string, [string, string]> = {
  on_screen: ["ok", "in the video"], left_out: ["muted", "left out"], folded: ["warn", "folded into a paragraph"], unplaced: ["bad", "unplaced"],
};

export default function Board({ id, onChange }: { id: string; onChange?: () => void }) {
  const { data: b, reload, error } = useApi<any>(`/api/board/${encodeURIComponent(id)}`);
  const [f, setF] = useState<F>("video");
  const act = useAct();
  const [stamp, setStamp] = useState(Date.now());
  if (error) return <div className="banner bad">{error}</div>;
  if (!b) return <Empty>Loading the board…</Empty>;
  const run = async (path: string, body: any, ok?: string) => {
    await act(async () => { await api(path, body); await reload(); setStamp(Date.now()); onChange?.(); }, ok);
  };
  const segs: any[] = b.segments;
  const flagged = segs.filter((s) => s.flags.length || (s.crop.status === "sub" && (s.crop.keeps ?? 100) < 30));
  const outPanels = b.panels.filter((p: any) => p.place !== "on_screen");
  const sm = b.summary;
  const shown = f === "video" ? segs.filter((s) => s.in_video) : f === "flagged" ? flagged : f === "all" ? segs : [];
  return (
    <Card pad={false}
      title={<div className="row small"><b>{mmss(sm.runtime)}</b><span className="muted">· {sm.in_video} of {sm.segments} segments in the video · {sm.panels} panels ({sm.left_out} left out, {sm.folded} folded) · {sm.long_holds} long holds</span></div>}
      right={<div className="row">
        <Busy className="sm" onClick={() => run("/api/storyboard/undo", {}, "Undone")} title="undo the last board change">↶ Undo</Busy>
      </div>}>
      <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)" }}>
        <Chips<F> value={f} onChange={setF} items={[["video", `In the video (${sm.in_video})`], ["flagged", `Needs a look (${flagged.length})`],
          ["out", `Not in the video (${outPanels.length} panels)`], ["all", `All segments (${sm.segments})`]]} />
      </div>
      {f === "out" ? (
        <div className="list">
          {outPanels.length === 0 ? <Empty>Every panel is in the video.</Empty> : outPanels.map((p: any) => (
            <div className="brow out" key={p.panel_id}>
              <img src={`/panelimg/${encodeURIComponent(p.panel_id)}?thumb=1`} alt="" loading="lazy" />
              <div className="txt">
                <div className="row"><Pill tone={PLACE[p.place][0]}>{PLACE[p.place][1]}</Pill><span className="small faint">#{p.n} · {p.panel_id} · {p.width}×{p.height}</span></div>
                {p.left_out_why && <div className="meta">Why: {p.left_out_why}</div>}
                {p.unit_text && <div className="meta">Folded into ¶{p.unit}: {p.unit_text}</div>}
                {p.ocr && <div className="meta">Text in the picture: {p.ocr}</div>}
                <div className="meta">{p.seen}</div>
              </div>
              <div className="ctl">
                <Busy className="sm" onClick={() => run("/api/storyboard/include", { panel_id: p.panel_id }, "Put in the video as a silent hold")}>Put in video</Busy>
              </div>
            </div>
          ))}
        </div>
      ) : shown.length === 0 ? <Empty>{f === "flagged" ? "Nothing needs a look." : "No segments."}</Empty> : (
        <div className="list">
          {shown.map((s) => <SegRow key={s.seg_index} s={s} b={b} stamp={stamp} run={run} />)}
        </div>
      )}
    </Card>
  );
}

function SegRow({ s, b, stamp, run }: { s: any; b: any; stamp: number; run: (p: string, body: any, ok?: string) => Promise<void> }) {
  const [edit, setEdit] = useState(false);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [dur, setDur] = useState(String(s.dur));
  const [menu, setMenu] = useState(false);
  const [swap, setSwap] = useState(false);
  const [add, setAdd] = useState<string | null>(null);
  const [moveTo, setMoveTo] = useState<string | null>(null);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => setDur(String(s.dur)), [s.dur]);
  useEffect(() => {
    if (!menu) return;
    const h = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setMenu(false); };
    document.addEventListener("mousedown", h);
    return () => document.removeEventListener("mousedown", h);
  }, [menu]);
  const panel = b.panels.find((p: any) => p.panel_id === s.panel_id) || {};
  const text = s.beats.map((x: any) => x.text).join(" ");
  return (
    <div className={`brow ${s.in_video ? "" : "out"}`}>
      <a href={`/segimg/${s.seg_index}`} target="_blank" rel="noreferrer" title="the exact frame the video shows">
        <img src={`/thumb/${s.seg_index}?t=${stamp}`} alt="" loading="lazy" />
      </a>
      <div className="txt">
        <div className="row small">
          <b>{s.video_start != null ? `${mmss(s.video_start)}–${mmss(s.video_start + s.dur)}` : "not in the video"}</b>
          <span className="faint">seg {s.seg_index} · {s.motion}</span>
          {s.flags.map((x: string) => <Pill key={x} tone="warn">{x}</Pill>)}
          {s.crop.status === "sub" && <Pill tone={(s.crop.keeps ?? 100) < 30 ? "bad" : (s.crop.keeps ?? 100) < 60 ? "warn" : "muted"}>crop keeps {s.crop.keeps}%</Pill>}
          {s.crop.full_override && <Pill>full panel</Pill>}
          {s.review === "rejected" && <Pill tone="bad">rejected</Pill>}
        </div>
        {edit ? (
          <div className="grid" style={{ gap: 6 }}>
            {s.beats.map((x: any) => (
              <textarea key={x.index} defaultValue={x.text} style={{ minHeight: 60 }}
                onChange={(e) => setTexts({ ...texts, [x.index]: e.target.value })} aria-label={`Line ${x.index}`} />
            ))}
            <div className="row">
              <Busy className="sm primary" onClick={async () => {
                const beats = s.beats.map((x: any) => ({ index: x.index, text: texts[x.index] ?? x.text }));
                await run(`/api/segments/${s.seg_index}/narration`, { beats }, "Saved — the line is re-recorded");
                setEdit(false);
              }}>Save (re-records the voice)</Busy>
              <button className="sm ghost" onClick={() => setEdit(false)}>Cancel</button>
            </div>
          </div>
        ) : (
          <div className="nar" onDoubleClick={() => s.beats.length && setEdit(true)} title={s.beats.length ? "double-click to edit" : ""}>
            {text || <span className="muted">Silent hold (no narration)</span>}
          </div>
        )}
        <details className="meta"><summary>Picture: {panel.panel_id}</summary>
          {panel.ocr && <div>Text in the picture: {panel.ocr}</div>}
          <div>{panel.seen}</div>
        </details>
        {swap && (
          <div className="row">
            <select defaultValue="" onChange={(e) => e.target.value && run("/api/storyboard/assign", { seg_index: s.seg_index, panel_id: e.target.value }, "Picture swapped").then(() => setSwap(false))} aria-label="Choose a picture">
              <option value="">Choose a different picture…</option>
              {b.panels.filter((p: any) => p.role === "art" || p.role === undefined).map((p: any) => <option key={p.panel_id} value={p.panel_id}>#{p.n} {p.panel_id} — {(p.seen || "").slice(0, 70)}</option>)}
            </select>
            <button className="sm ghost" onClick={() => setSwap(false)}>Cancel</button>
          </div>
        )}
        {moveTo !== null && (
          <div className="row">
            <input value={moveTo} onChange={(e) => setMoveTo(e.target.value)} placeholder="Position in the video (1 = first)" inputMode="numeric" style={{ maxWidth: 240 }} />
            <Busy className="sm primary" disabled={!/^\d+$/.test(moveTo)} onClick={async () => { await run("/api/storyboard/move", { seg_index: s.seg_index, to: Math.max(0, Number(moveTo) - 1) }, "Moved"); setMoveTo(null); }}>Move</Busy>
            <button className="sm ghost" onClick={() => setMoveTo(null)}>Cancel</button>
          </div>
        )}
        {add !== null && (
          <div className="row">
            <input value={add} onChange={(e) => setAdd(e.target.value)} placeholder="A new line of narration after this one" />
            <Busy className="sm primary" disabled={!add.trim()} onClick={async () => { await run("/api/storyboard/addline", { seg_index: s.seg_index, text: add.trim() }, "Line added"); setAdd(null); }}>Add</Busy>
            <button className="sm ghost" onClick={() => setAdd(null)}>Cancel</button>
          </div>
        )}
      </div>
      <div className="ctl">
        <label className="check small"><input type="checkbox" checked={s.in_video}
          onChange={(e) => run("/api/storyboard/set_included", { panel_id: s.panel_id, included: e.target.checked })} /> in video</label>
        <div className="row" style={{ flexWrap: "nowrap" }}>
          <input value={dur} onChange={(e) => setDur(e.target.value)} style={{ width: 64, minHeight: 30, padding: "4px 6px" }} aria-label="Seconds on screen"
            onKeyDown={(e) => e.key === "Enter" && run("/api/storyboard/duration", { seg_index: s.seg_index, dur: Number(dur) }, "Length set")} />
          <span className="small muted">s</span>
          <Busy className="sm" onClick={() => run("/api/storyboard/duration", { seg_index: s.seg_index, dur: Number(dur) }, "Length set")}>Set</Busy>
        </div>
        <div className="row">
          {s.beats.length > 0 && <button className="sm" onClick={() => setEdit(true)}>✎ Edit</button>}
          <div className="menu" ref={ref}>
            <button className="sm" onClick={() => setMenu(!menu)} aria-expanded={menu}>More ▾</button>
            {menu && (
              <div className="pop" onClick={() => setMenu(false)}>
                <button onClick={() => setSwap(true)}>🖼 Swap the picture</button>
                <button onClick={() => setAdd("")}>＋ Add a line after</button>
                {s.beats.map((x: any) => (
                  <button key={x.index} onClick={() => run("/api/storyboard/delline", { seg_index: s.seg_index, beat_index: x.index }, "Line removed")}>✕ Remove line “{x.text.slice(0, 28)}…”</button>
                ))}
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: -1 }, "Cut moved")}>⇤ Cut 1 s earlier</button>
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: 1 }, "Cut moved")}>⇥ Cut 1 s later</button>
                {s.crop.status !== "full" && <button onClick={() => run("/api/storyboard/use_full_panel", { seg_index: s.seg_index }, "Whole panel used")}>▣ Use the whole panel</button>}
                {s.crop.restorable && <button onClick={() => run("/api/storyboard/restore_crop", { seg_index: s.seg_index }, "Crop restored")}>✂ Restore the planned crop</button>}
                <button onClick={() => run("/api/storyboard/duplicate", { seg_index: s.seg_index }, "Duplicated")}>⧉ Duplicate</button>
                <button onClick={() => setMoveTo("")}>↕ Move to position…</button>
              </div>
            )}
          </div>
          <ConfirmButton className="sm danger" confirm="Delete?" onConfirm={() => run("/api/storyboard/delete", { seg_index: s.seg_index }, "Deleted")}>🗑</ConfirmButton>
        </div>
      </div>
    </div>
  );
}
