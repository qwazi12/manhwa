"use client";
import { useEffect, useMemo, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { mmss } from "@/lib/fmt";
import { Busy, Card, Chips, ConfirmButton, Empty, Pill, useAct } from "@/components/ui";

type F = "video" | "flagged" | "out" | "all";
const PLACE: Record<string, [string, string]> = {
  on_screen: ["ok", "in the video"], left_out: ["muted", "left out"], folded: ["warn", "folded into a paragraph"], unplaced: ["bad", "unplaced"],
};

/** Check the board: every segment in video order. Everything the classic board
 *  did, with the secondary actions in a More menu (owner, 2026-10-04). */
export default function Board({ id, onChange, sentBack, builtWith }:
  { id: string; onChange?: () => void; sentBack?: { notes: string } | null; builtWith?: any }) {
  const { data: b, reload, error } = useApi<any>(`/api/board/${encodeURIComponent(id)}`);
  const [f, setF] = useState<F>("video");
  const act = useAct();
  const [stamp, setStamp] = useState(Date.now());
  const [drag, setDrag] = useState<number | null>(null);
  const [over, setOver] = useState<number | null>(null);
  const [swapFor, setSwapFor] = useState<any>(null);
  if (error) return <div className="banner bad">{error}</div>;
  if (!b) return <Empty>Loading the board…</Empty>;
  const run = async (path: string, body: any, ok?: string) => {
    await act(async () => { await api(path, body); await reload(); setStamp(Date.now()); onChange?.(); }, ok);
  };
  const segs: any[] = b.segments;
  const pos = (si: number) => segs.findIndex((s) => s.seg_index === si);
  const flagged = segs.filter((s) => s.flags.length || (s.crop.status === "sub" && (s.crop.keeps ?? 100) < 30) || s.silent);
  const outPanels = b.panels.filter((p: any) => p.place !== "on_screen");
  const sm = b.summary;
  const shown = f === "video" ? segs.filter((s) => s.in_video) : f === "flagged" ? flagged : f === "all" ? segs : [];
  const canDrag = f === "video" || f === "all";
  return (
    <>
      {sentBack && <div className="banner warn"><b>↩ The last video was sent back.</b> {sentBack.notes || "No notes."} Fix it here, then Render again.</div>}
      <Card pad={false}
        title={<div className="row small"><b>{mmss(sm.runtime)}</b><span className="muted">· {sm.in_video} of {sm.segments} segments in the video · {sm.panels} panels</span></div>}
        right={<div className="row">
          <Busy className="sm" onClick={() => run("/api/storyboard/set_included", { all: true, included: true }, "Every segment ticked")}>Tick all</Busy>
          <ConfirmButton className="sm" confirm="Untick every segment?" onConfirm={() => run("/api/storyboard/set_included", { all: true, included: false }, "Every segment unticked")}>Tick none</ConfirmButton>
          <Busy className="sm" onClick={() => run("/api/storyboard/undo", {}, "Undone")} title="undo the last board change">↶ Undo</Busy>
        </div>}>
        <div style={{ padding: "10px 14px", borderBottom: "1px solid var(--border)", display: "grid", gap: 8 }}>
          <div className="row small">
            <Pill tone={sm.silent ? "warn" : "ok"}>{sm.silent ? `${sm.silent} silent hold(s)` : "no dead air"}</Pill>
            <Pill tone={sm.folded ? "warn" : "muted"}>{sm.folded} folded</Pill>
            <Pill tone={sm.unplaced ? "bad" : "muted"}>{sm.unplaced} not on the timeline</Pill>
            <Pill tone="muted">{sm.left_out} left out</Pill>
            <Pill tone={sm.long_holds ? "warn" : "muted"}>{sm.long_holds} long hold(s)</Pill>
            {builtWith && <span className="faint">built with: {[builtWith.engine, builtWith.match, builtWith.split, builtWith.pages ? `${builtWith.pages} pages` : null].filter(Boolean).join(" · ")}</span>}
            {builtWith?.scrape_warning && <span style={{ color: "var(--yellow)" }}>⚠ {builtWith.scrape_warning}</span>}
          </div>
          <Chips<F> value={f} onChange={setF} items={[["video", `In the video (${sm.in_video})`], ["flagged", `Needs a look (${flagged.length})`],
            ["out", `Not in the video (${outPanels.length} panels)`], ["all", `All segments (${sm.segments})`]]} />
          {canDrag && <span className="small faint">Drag a row by ⠿ to move it. On a phone, use More → Move up / Move down.</span>}
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
            {shown.map((s) => (
              <div key={s.seg_index}
                onDragOver={(e) => { if (drag !== null) { e.preventDefault(); setOver(s.seg_index); } }}
                onDragLeave={() => setOver((o) => (o === s.seg_index ? null : o))}
                onDrop={(e) => { e.preventDefault(); if (drag !== null && drag !== s.seg_index) run("/api/storyboard/move", { seg_index: drag, to: pos(s.seg_index) }, "Moved"); setDrag(null); setOver(null); }}
                style={over === s.seg_index ? { boxShadow: "inset 0 3px 0 var(--blue)" } : undefined}>
                <SegRow s={s} b={b} stamp={stamp} run={run} canDrag={canDrag}
                  onDragStart={() => setDrag(s.seg_index)} onDragEnd={() => { setDrag(null); setOver(null); }}
                  onSwap={() => setSwapFor(s)} index={pos(s.seg_index)} last={segs.length - 1} />
              </div>
            ))}
          </div>
        )}
      </Card>
      {swapFor && <SwapPicker seg={swapFor} panels={b.panels} onClose={() => setSwapFor(null)}
        onPick={async (pid: string) => { await run(`/api/segments/${swapFor.seg_index}/panel`, { panel_id: pid }, "Picture swapped"); setSwapFor(null); }} />}
    </>
  );
}

function SegRow({ s, b, stamp, run, canDrag, onDragStart, onDragEnd, onSwap, index, last }: any) {
  const [edit, setEdit] = useState(false);
  const [texts, setTexts] = useState<Record<number, string>>({});
  const [dur, setDur] = useState(String(s.dur));
  const [menu, setMenu] = useState(false);
  const [add, setAdd] = useState<string | null>(null);
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
  const st = s.review;
  return (
    <div className={`brow ${s.in_video ? "" : "out"}`}>
      <div style={{ display: "grid", gap: 6, justifyItems: "start" }}>
        <a href={`/segimg/${s.seg_index}`} target="_blank" rel="noreferrer" title="the exact frame the video shows">
          <img src={`/thumb/${s.seg_index}?t=${stamp}`} alt="" loading="lazy" />
        </a>
        <div className="row small" style={{ gap: 4 }}>
          {s.crop.status === "sub" && <Pill tone={(s.crop.keeps ?? 100) < 30 ? "bad" : (s.crop.keeps ?? 100) < 60 ? "warn" : "muted"}>✂ keeps {s.crop.keeps}%</Pill>}
          {s.crop.status === "tiny" && <Pill tone="bad">crop too small</Pill>}
          {s.crop.status === "full" && <Pill>▣ full panel{s.crop.full_override ? " (yours)" : ""}</Pill>}
        </div>
        {(s.crop.status === "sub" || s.crop.status === "tiny") && <Busy className="sm" onClick={() => run("/api/storyboard/use_full_panel", { seg_index: s.seg_index }, "Whole panel used")}>Use full panel</Busy>}
        {s.crop.restorable && <Busy className="sm ghost" onClick={() => run("/api/storyboard/restore_crop", { seg_index: s.seg_index }, "Crop restored")}>Restore crop</Busy>}
        <a className="small" href={`/segimg/${s.seg_index}?full=1`} target="_blank" rel="noreferrer">original ↗</a>
      </div>
      <div className="txt">
        <div className="row small">
          {canDrag && <span draggable onDragStart={(e) => { e.dataTransfer.effectAllowed = "move"; onDragStart(); }} onDragEnd={onDragEnd}
            title="drag to move" style={{ cursor: "grab", fontSize: 18, lineHeight: 1, userSelect: "none" }} aria-label="Drag to move">⠿</span>}
          <b>{s.video_start != null ? `${mmss(s.video_start)}–${mmss(s.video_start + s.dur)}` : "not in the video"}</b>
          <span className="faint">seg {s.seg_index} · {s.motion}</span>
          {s.flags.map((x: string) => <Pill key={x} tone="warn">{x}</Pill>)}
          {st === "approved" && <Pill tone="ok">✓ approved</Pill>}
          {st === "rejected" && <Pill tone="bad">✕ rejected</Pill>}
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
        <details className="meta"><summary>Picture {panel.panel_id}{panel.ocr ? " · has text" : ""}</summary>
          {panel.ocr && <div><b>Text in the picture:</b> {panel.ocr}</div>}
          <div><b>What the AI saw:</b> {panel.seen}</div>
        </details>
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
          <button className={`sm ${st === "approved" ? "primary" : ""}`} title="approve this segment"
            onClick={() => run(`/api/segments/${s.seg_index}/status`, { status: st === "approved" ? "pending" : "approved", note: "" })}>✓</button>
          <button className={`sm ${st === "rejected" ? "danger" : ""}`} title="reject: take it out of the video (one click back)"
            onClick={() => run(`/api/segments/${s.seg_index}/status`, { status: st === "rejected" ? "pending" : "rejected", note: "" })}>✕</button>
          <button className="sm" onClick={onSwap}>🖼 Swap</button>
          {s.beats.length > 0 && <button className="sm" onClick={() => setEdit(true)}>✎ Edit</button>}
          <div className="menu" ref={ref}>
            <button className="sm" onClick={() => setMenu(!menu)} aria-expanded={menu}>More ▾</button>
            {menu && (
              <div className="pop" onClick={() => setMenu(false)}>
                <button onClick={() => setAdd("")}>＋ Add a line after</button>
                {s.beats.map((x: any) => (
                  <button key={x.index} onClick={() => run("/api/storyboard/delline", { seg_index: s.seg_index, beat_index: x.index }, "Line removed")}>✕ Remove line “{x.text.slice(0, 28)}…”</button>
                ))}
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: -1 }, "Cut moved")}>⇤ Cut 1 s earlier</button>
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: -0.25 }, "Cut moved")}>⇤ Cut ¼ s earlier</button>
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: 0.25 }, "Cut moved")}>⇥ Cut ¼ s later</button>
                <button onClick={() => run("/api/storyboard/boundary", { seg_index: s.seg_index, delta: 1 }, "Cut moved")}>⇥ Cut 1 s later</button>
                <button disabled={index <= 0} onClick={() => run("/api/storyboard/move", { seg_index: s.seg_index, to: index - 1 }, "Moved up")}>↑ Move up</button>
                <button disabled={index >= last} onClick={() => run("/api/storyboard/move", { seg_index: s.seg_index, to: index + 1 }, "Moved down")}>↓ Move down</button>
                <button onClick={() => run("/api/storyboard/duplicate", { seg_index: s.seg_index }, "Duplicated")}>⧉ Duplicate</button>
                <button onClick={() => run("/api/storyboard/delete", { seg_index: s.seg_index }, "Segment deleted")}>🗑 Delete the segment</button>
              </div>
            )}
          </div>
        </div>
      </div>
    </div>
  );
}

/** Pick a new picture by seeing it: the matcher's suggestions first, then every
 *  usable picture of the chapter, searchable. */
function SwapPicker({ seg, panels, onClose, onPick }: any) {
  const sug = useApi<any>(`/api/segments/${seg.seg_index}/candidates?k=8`);
  const [q, setQ] = useState("");
  const [busy, setBusy] = useState<string | null>(null);
  const all = useMemo(() => panels.filter((p: any) => (p.role || "art") === "art" &&
    (!q || `${p.panel_id} ${p.seen} ${p.ocr}`.toLowerCase().includes(q.toLowerCase()))), [panels, q]);
  const pick = async (pid: string) => { setBusy(pid); await onPick(pid); setBusy(null); };
  const Tile = ({ p, note }: { p: any; note?: string }) => (
    <button className={`tc ${p.panel_id === seg.panel_id ? "on" : ""}`} disabled={!!busy || p.panel_id === seg.panel_id}
      onClick={() => pick(p.panel_id)} title={p.seen || p.desc} style={{ padding: 0, display: "grid", textAlign: "left", background: "var(--panel)" }}>
      <img src={`/panelimg/${encodeURIComponent(p.panel_id)}?thumb=1`} alt="" loading="lazy" style={{ aspectRatio: "auto", maxHeight: 180, objectFit: "contain", background: "var(--panel2)" }} />
      <span className="m"><b>{p.panel_id === seg.panel_id ? "current" : busy === p.panel_id ? "swapping…" : p.panel_id}</b>
        <span className="faint">{(note || p.seen || p.desc || "").slice(0, 70)}</span></span>
    </button>
  );
  return (
    <div role="dialog" aria-label="Swap the picture" style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(0,0,0,.55)", display: "grid", placeItems: "center", padding: 16 }}
      onClick={(e) => { if (e.target === e.currentTarget) onClose(); }}>
      <div className="card" style={{ width: "min(1100px, 100%)", maxHeight: "90vh", overflowY: "auto" }}>
        <div className="hd"><h2>Swap the picture · seg {seg.seg_index}</h2><button className="sm" onClick={onClose}>Close</button></div>
        <div className="bd">
          <div className="small muted">“{seg.beats.map((x: any) => x.text).join(" ").slice(0, 200)}”</div>
          <h3>Best matches for this line</h3>
          {!sug.data ? <Empty>Loading…</Empty> : (
            <div className="thumbs" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
              {sug.data.candidates.map((c: any) => <Tile key={c.panel_id} p={c} note={`match ${Math.round(c.score * 100)}% · ${c.desc}`} />)}
            </div>
          )}
          <div className="spread"><h3>Every picture ({all.length})</h3><input placeholder="Search pictures" value={q} onChange={(e) => setQ(e.target.value)} style={{ maxWidth: 260 }} /></div>
          <div className="thumbs" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(150px, 1fr))" }}>
            {all.map((p: any) => <Tile key={p.panel_id} p={p} />)}
          </div>
        </div>
      </div>
    </div>
  );
}
