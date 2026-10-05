"use client";
import { useState } from "react";
import { api, useApi } from "@/lib/api";
import { Card, useAct } from "./ui";

type Opts = { engine: string; variant: string; fresh: boolean; direct: "" | "on" | "off"; voice: string; style: string };
const DEFAULTS: Opts = { engine: "gemini", variant: "", fresh: false, direct: "", voice: "", style: "" };

/** One box for any Asura or WEBTOON series or chapter link. A chapter link can
 *  be made now; Options holds what the old Ingest page offered (engine, saved
 *  version, fresh rebuild, direct speech, a voice for this chapter only). */
export default function PasteBox({ onDone }: { onDone?: () => void }) {
  const [url, setUrl] = useState("");
  const [tier, setTier] = useState("greenlight");
  const [make, setMake] = useState(true);
  const [open, setOpen] = useState(false);
  const [o, setO] = useState<Opts>(DEFAULTS);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const voices = useApi<any>(open ? "/api/voices" : null);
  const act = useAct();
  const ch = /\/chapter\/(\d+(?:\.\d+)?)\/?$/i.exec(url.trim()) || /[?&]episode_no=(\d+)/i.exec(url.trim());
  const custom = JSON.stringify(o) !== JSON.stringify(DEFAULTS);
  async function go() {
    const u = url.trim();
    if (!/^https?:\/\//.test(u)) { setMsg("Paste a full link (https://…)."); return; }
    if (o.variant && !/^[a-z0-9-]{1,16}$/.test(o.variant)) { setMsg("A version name is 1–16 lowercase letters, digits or dashes, like v2."); return; }
    setBusy(true); setMsg("Reading the page…");
    // with options, the chapter goes through the full ingest; tracking still happens
    const r = await act(() => api("/api/watchlist/quick", { url: u, tier, make_chapter: !!ch && make && !custom }));
    let note = "";
    if (r && ch && make && custom) {
      const j = await act(() => api("/api/ingest", {
        url: u, queue: true, fresh: o.fresh, engine: o.engine, variant: o.variant,
        direct_speech: o.direct === "" ? null : o.direct === "on", voice: o.voice, voice_style: o.voice ? o.style : null,
      }));
      if (j) note = j.existing ? " That chapter is already being made." : ` Making ch.${ch[1]}${o.variant ? ` as version ${o.variant}` : ""} now.`;
    }
    setBusy(false);
    if (!r) { setMsg(null); return; }
    setMsg((r.created ? `Added ${r.title} (${r.source}).` : `${r.title} is already tracked.`) +
      " Chapters and cast are being checked in the background." +
      (note || (r.job ? ` Making ch.${r.chapter} now.` : r.note ? ` ${r.note}.` : "")));
    setUrl("");
    setO(DEFAULTS);
    onDone?.();
  }
  const set = (p: Partial<Opts>) => setO({ ...o, ...p });
  return (
    <Card>
      <div className="row" style={{ alignItems: "stretch" }}>
        <input id="paste" type="url" inputMode="url" placeholder="Paste any Asura or WEBTOON series or chapter link"
          value={url} onChange={(e) => setUrl(e.target.value)} onKeyDown={(e) => e.key === "Enter" && go()}
          style={{ flex: 1, minWidth: 220 }} aria-label="Series or chapter link" />
        <select id="pastetier" value={tier} onChange={(e) => setTier(e.target.value)} style={{ width: "auto" }} aria-label="Priority">
          <option value="greenlight">Make now</option><option value="high_upside">Next up</option><option value="watchlist">Watching</option>
        </select>
        <button className="primary" onClick={go} disabled={busy}>{busy ? "…" : "Add"}</button>
      </div>
      {ch && (
        <div className="spread">
          <label className="check"><input type="checkbox" checked={make} onChange={(e) => setMake(e.target.checked)} />
            Also make ch.{ch[1]} now (about $0.50)</label>
          {make && <button className="sm ghost" onClick={() => setOpen(!open)} aria-expanded={open}>Options {custom ? "(changed)" : ""} {open ? "▴" : "▾"}</button>}
        </div>
      )}
      {ch && make && open && (
        <div className="grid g3" style={{ gap: 10 }}>
          <label className="field">Made by<select id="engine" value={o.engine} onChange={(e) => set({ engine: e.target.value, ...(e.target.value === "claude" ? { variant: "", direct: "" } : {}) })}>
            <option value="gemini">Gemini (standard)</option><option value="claude">Claude lab (experimental)</option></select></label>
          <label className="field">Save as a version<input id="variant" value={o.variant} disabled={o.engine === "claude"} placeholder="leave empty, or v2"
            onChange={(e) => set({ variant: e.target.value.toLowerCase() })} /></label>
          <label className="field">Direct speech (quoted lines)<select id="direct" value={o.direct} disabled={o.engine === "claude"} onChange={(e) => set({ direct: e.target.value as Opts["direct"] })}>
            <option value="">Studio default</option><option value="on">On</option><option value="off">Off</option></select></label>
          <label className="field">Voice for this chapter only<select id="chvoice" value={o.voice} onChange={(e) => set({ voice: e.target.value })}>
            <option value="">Studio default{voices.data ? ` (${voices.data.default.id.split(":")[1]})` : ""}</option>
            {(voices.data?.voices || []).map((v: any) => <option key={v.id} value={v.id}>{v.label}</option>)}</select></label>
          {o.voice && !o.voice.startsWith("chirp:") && (
            <label className="field">Style<select id="chstyle" value={o.style} onChange={(e) => set({ style: e.target.value })}>
              {(voices.data?.styles || []).map((s: any) => <option key={s.style} value={s.style}>{s.label}</option>)}</select></label>
          )}
          <label className="check" style={{ alignSelf: "end" }}><input type="checkbox" checked={o.fresh} onChange={(e) => set({ fresh: e.target.checked })} />
            Make it again from scratch (new script and voice)</label>
        </div>
      )}
      {msg && <div className="small muted">{msg}</div>}
    </Card>
  );
}
