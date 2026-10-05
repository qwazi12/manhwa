"use client";
import { useState } from "react";
import { api } from "@/lib/api";
import { Card, useAct } from "./ui";

/** One box for any Asura or WEBTOON series or chapter link. */
export default function PasteBox({ onDone }: { onDone?: () => void }) {
  const [url, setUrl] = useState("");
  const [tier, setTier] = useState("greenlight");
  const [make, setMake] = useState(true);
  const [msg, setMsg] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const act = useAct();
  const ch = /\/chapter\/(\d+(?:\.\d+)?)\/?$/i.exec(url.trim()) || /[?&]episode_no=(\d+)/i.exec(url.trim());
  async function go() {
    if (!/^https?:\/\//.test(url.trim())) { setMsg("Paste a full link (https://…)."); return; }
    setBusy(true); setMsg("Reading the page…");
    const r = await act(() => api("/api/watchlist/quick", { url: url.trim(), tier, make_chapter: !!ch && make }));
    setBusy(false);
    if (!r) { setMsg(null); return; }
    setMsg((r.created ? `Added ${r.title} (${r.source}).` : `${r.title} is already tracked.`) +
      " Chapters and cast are being checked in the background." +
      (r.job ? ` Making ch.${r.chapter} now.` : r.note ? ` ${r.note}.` : ""));
    setUrl("");
    onDone?.();
  }
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
        <label className="check"><input type="checkbox" checked={make} onChange={(e) => setMake(e.target.checked)} />
          Also make ch.{ch[1]} now (about $0.50)</label>
      )}
      {msg && <div className="small muted">{msg}</div>}
    </Card>
  );
}
