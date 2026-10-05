"use client";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, mmss, when } from "@/lib/fmt";
import { Busy, Card, ConfirmButton, Empty, Pill, StatusPill, Status, useAct, useToast } from "@/components/ui";

function QualityCard({ qc }: { qc: any }) {
  const v = qc.validation || {};
  return (
    <Card title="Quality checks">
      <div className="kv"><span>Pictures matched by</span><span>{qc.semantic ? "meaning (embeddings)" : <b style={{ color: "var(--red)" }}>word overlap only</b>} · {qc.match_method}</span></div>
      {qc.embed_fallback_reason && <div className="kv"><span>Why</span><span>{qc.embed_fallback_reason}</span></div>}
      <div className="kv"><span>In the video</span><span>{qc.segments_in_video} of {qc.segments_total} segments · {Math.round(qc.runtime_s)} s</span></div>
      <div className="kv"><span>Timing check</span><span>{v.ok ? "passes" : `${(v.errors || []).length} error(s)`} · {(v.warnings || []).length} warning(s)</span></div>
      <div className="kv"><span>Long holds (&gt;12 s on one picture)</span><span>{(qc.long_holds || []).length ? qc.long_holds.map((h: any) => `${h.panel} ${h.seconds}s`).join(", ") : "none"}</span></div>
      <div className="kv"><span>Silent segments</span><span>{(qc.silent_segments || []).length || "none"}</span></div>
      {qc.scrape_warning && <div className="kv"><span>Download</span><span style={{ color: "var(--yellow)" }}>{qc.scrape_warning}</span></div>}
      {qc.render_job && <div className="kv"><span>Render</span><span>{qc.render_job.clips} clips · {qc.render_job.status}</span></div>}
    </Card>
  );
}

/** Watch, approve, and fill in what gets published — one column, the same
 *  parts as every other page. A suggestion fills only its own field. */
export default function VideoTab({ id, name, status, onChange, draft = false }: { id: string; name: string; status: Status; onChange?: () => void; draft?: boolean }) {
  const q = `project=${enc(id)}&name=${enc(name)}`;
  const rv = useApi<any>(draft ? null : `/api/review?${q}`);
  const pub = useApi<any>(`/api/publish?${q}`);
  const pst = useApi<any>(draft ? null : `/api/publishing/publish/status?${q}`, 10000);
  const acc = useApi<any>("/api/publishing/status");
  const act = useAct();
  const toast = useToast();
  const [md, setMd] = useState<any>(null);
  const [notes, setNotes] = useState("");
  const [cb, setCb] = useState(Date.now());
  useEffect(() => { if (pub.data?.metadata && !md) setMd(pub.data.metadata); }, [pub.data]);
  useEffect(() => { if (rv.data?.review) setNotes(rv.data.review.notes || ""); }, [rv.data?.review?.reviewed_at]);
  // Like Scrapper: a video gets its title, description, tags and thumbnail
  // without a click. New renders are prepared by the server; a video made
  // before that is prepared once when it is opened here (owner, 2026-10-05).
  const prep = useRef<{ asked: boolean; started: number }>({ asked: false, started: 0 });
  const [preparing, setPreparing] = useState(false);
  useEffect(() => {
    const d = pub.data;
    if (draft || !d || d.missing) return;
    const need = !d.seo || !(d.thumbcopilot?.concepts?.concepts || []).length || !d.thumbnail?.width;
    if (need && !prep.current.asked) {
      prep.current = { asked: true, started: Date.now() };
      setPreparing(true);
      api("/api/publish/prepare", { project: id, name }).catch(() => setPreparing(false));
    }
    if (!prep.current.started) return;
    if (d.preparing || Date.now() - prep.current.started < 5000) {
      const t = setTimeout(pub.reload, 2500);
      return () => clearTimeout(t);
    }
    prep.current.started = 0;
    setPreparing(false);
    setMd(d.metadata);            // show what was filled in
    setCb(Date.now());
  }, [pub.data]);
  if ((!draft && !rv.data) || !pub.data || !md) return <Empty>Loading…</Empty>;
  const p = pub.data;
  const review = rv.data?.review || {};
  const rec = pst.data?.publish;
  const posted = (rec?.results || []).filter((r: any) => r.status === "published");
  const lim = p.limits || { title: 100, description: 5000 };

  async function save(patch: any, ok?: string) {
    const next = { ...md, ...patch };
    setMd(next);
    const r = await act(() => api("/api/publish", { project: id, name, metadata: next }), ok);
    if (r?.metadata) setMd(r.metadata);
  }
  async function verdict(st: string) {
    const r = await act(() => api("/api/review", { project: id, name, status: st, notes }),
      st === "approved" ? "Approved — scheduled for the next free slot" : st === "sent_back" ? "Sent back — taken off the queue" : "Notes saved");
    if (r) { rv.reload(); onChange?.(); }
  }
  async function applySeo(field: string, value?: any, variant?: string) {
    const r = await act(() => api("/api/seo/apply", { project: id, name, field, value: value ?? null, variant: variant || "" }), "Filled in");
    if (r?.metadata) setMd(r.metadata);
  }
  const seo = p.seo;
  const tc = p.thumbcopilot || {};
  const concepts = tc.concepts?.concepts || [];
  const chosen = tc.concepts?.chosen?.concept_id;
  const accounts = (acc.data?.accounts || []).filter((a: any) => a.active);

  return (
    <div className="vgrid">
      <div className="vcol vleft">
        {!draft && <Card title="Watch" right={<span className="small muted">{rv.data.stat?.size_mb} MB · {name}</span>}>
          <video controls playsInline preload="metadata" src={`/export/${enc(name)}?project=${enc(id)}&s=${rv.data?.stat?.size_mb ?? ""}`} />
          {review.superseded && <div className="banner warn">The board changed after this video was approved. Render again, then approve the new video.</div>}
          <label className="field">Notes for yourself or the next edit
            <textarea id="notes" value={notes} onChange={(e) => setNotes(e.target.value)} style={{ minHeight: 60 }} />
          </label>
          <div className="row">
            {status.key === "video_ready" || review.status !== "approved" ? (
              <Busy className="primary" onClick={() => verdict("approved")}>✓ Approve & schedule</Busy>
            ) : <StatusPill s={status} />}
            <Busy onClick={() => verdict("sent_back")}>↩ Send back</Busy>
            <Busy className="ghost" onClick={() => verdict("")}>Save notes</Busy>
            {review.reviewed_at && <span className="small muted">last decision {when(review.reviewed_at)}</span>}
            <a className="btn sm ghost" href={`/export/${enc(name)}?project=${enc(id)}`} download>Download video</a>
          </div>
          {(review.history || []).length > 0 && (
            <details><summary className="small muted">Earlier decisions ({review.history.length})</summary>
              {review.history.slice().reverse().map((h: any, i: number) => (
                <div key={i} className="small"><b>{h.status}</b> · {when(h.at)}{h.notes ? ` — ${h.notes}` : ""}</div>))}
            </details>
          )}
        </Card>}
        {!draft && rv.data.qc && <QualityCard qc={rv.data.qc} />}
        {!draft && <>
        <Card title="Post">
          {posted.length > 0 ? (
            <div className="grid" style={{ gap: 6 }}>
              {posted.map((r: any) => (
                <div key={r.account_id} className="row"><Pill tone="ok">✓ Posted</Pill><b>{r.username}</b>
                  {r.url?.startsWith("http") ? <a href={r.url} target="_blank" rel="noreferrer">{r.url}</a> :
                    r.platform_post_id ? <a href={`https://youtu.be/${r.platform_post_id}`} target="_blank" rel="noreferrer">youtu.be/{r.platform_post_id}</a> : null}
                  <span className="small muted">{when(r.published_at)}</span></div>
              ))}
            </div>
          ) : (
            <>
              {(p.readiness?.blockers || []).length > 0 ? (
                <div className="banner warn"><b>Not ready to post:</b><ul style={{ margin: "4px 0 0 18px", padding: 0 }}>{p.readiness.blockers.map((x: string, i: number) => <li key={i}>{x}</li>)}</ul></div>
              ) : <div className="banner ok">Ready. It posts at the next free slot, or now if you press Post now.</div>}
              {rec && (rec.results || []).length > 0 && (
                <div className="grid" style={{ gap: 4 }}>
                  {rec.results.map((r: any) => (
                    <div key={r.account_id} className="row small"><Pill tone={r.status === "published" ? "ok" : r.status === "failed" ? "bad" : "warn"}>{r.status}</Pill>
                      <b>{r.username || r.account_id}</b>{r.error && <span style={{ color: "var(--red)" }}>{r.error}</span>}</div>
                  ))}
                </div>
              )}
              {rec?.status === "in_progress" && <div className="banner info">Posting: {rec.stage}</div>}
              {rec?.status === "failed" && <div className="banner bad">Last try failed: {rec.error}</div>}
              <ConfirmButton className="primary" disabled={(p.readiness?.blockers || []).length > 0 || rec?.status === "in_progress"}
                confirm={`Post now as ${md.privacy}? It can't be taken back.`}
                onConfirm={() => act(async () => { await api("/api/publishing/publish", { project: id, name }); pst.reload(); onChange?.(); }, "Posting")}>Post now</ConfirmButton>
              <a className="btn sm ghost" href={`/api/publish/package?${q}`}>Download upload package</a>
              <span className="small faint">The package has the details, checklist and thumbnail for a manual upload; the video itself downloads from the player.</span>
            </>
          )}
        </Card>
        <p className="small faint">Runtime {mmss(rv.data.qc?.runtime_s)} · {rv.data.qc?.segments_in_video} segments · matched {rv.data.qc?.match_method}</p>
        </>}
      </div>
      <div className="vcol">
        {draft && <div className="banner info">Preparing the details before the render. Whatever you set here is used for the video when it’s made. Thumbnails come after the render, because they’re built from the video’s pictures.</div>}
        <Card title="What gets published">
          <label className="field">Title <span className="count num">{(md.title || "").length}/{lim.title}</span>
            <input id="title" value={md.title || ""} maxLength={lim.title} onChange={(e) => setMd({ ...md, title: e.target.value })} onBlur={() => save({})} />
          </label>
          {seo?.titles?.length > 0 && (
            <details open>
              <summary className="small muted">Title suggestions ({seo.titles.length})</summary>
              <div className="grid" style={{ gap: 6, marginTop: 6 }}>
                {seo.titles.map((t: any, i: number) => (
                  <div key={i} className="sugg">
                    <div className="spread"><b>{t.text}</b>
                      <button className={`sm ${t.recommended ? "primary" : ""}`} onClick={() => applySeo("title", t.text)}>Use</button></div>
                    <span className="small muted">{t.why}</span>
                    {t.score && <span className="small faint">score {t.score.total}/100 · relevance {t.score.relevance} · channel fit {t.score.channel_fit} · discovery {t.score.discovery} · hook {t.score.hook}{(t.influence || []).length ? ` · drawn from ${t.influence.join(", ")}` : ""}</span>}
                  </div>
                ))}
              </div>
            </details>
          )}
          <label className="field">Description <span className="count num">{(md.description || "").length}/{lim.description}</span>
            <textarea id="desc" value={md.description || ""} style={{ minHeight: 150 }} onChange={(e) => setMd({ ...md, description: e.target.value })} onBlur={() => save({})} />
          </label>
          {seo?.description && (
            <div className="sugg">
              <div className="spread"><span className="small muted">Suggested description</span>
                <span className="row"><button className="sm" onClick={() => applySeo("description")}>Use</button>
                  {seo.description_short && <button className="sm ghost" onClick={() => applySeo("description", null, "short")}>Use short</button>}</span></div>
              <div className="small" style={{ whiteSpace: "pre-wrap" }}>{seo.description.slice(0, 600)}{seo.description.length > 600 ? "…" : ""}</div>
            </div>
          )}
          <label className="field">Tags (comma separated)
            <input id="tags" value={(md.tags || []).join(", ")} onChange={(e) => setMd({ ...md, tags: e.target.value.split(",").map((x) => x.trimStart()) })}
              onBlur={() => save({ tags: (md.tags || []).map((x: string) => x.trim()).filter(Boolean) })} />
          </label>
          {seo?.tags?.length > 0 && (
            <div className="row small"><span className="muted">Suggested:</span>{seo.tags.slice(0, 12).map((t: string) => <Pill key={t}>{t}</Pill>)}
              <button className="sm" onClick={() => applySeo("tags")}>Use these tags</button>
              {seo.hashtags?.length > 0 && <button className="sm ghost" onClick={() => applySeo("hashtags")}>Add hashtags</button>}</div>
          )}
          {!preparing && !seo && p.prepare_note && <div className="banner warn">{p.prepare_note}</div>}
          {preparing && <div className="banner info">Filling in the title, description, tags and thumbnail from the chapter… (about 20 seconds)</div>}
          {!seo && !preparing && <Busy className="sm" onClick={() => act(async () => { await api("/api/seo/generate", { project: id, name }); pub.reload(); }, "Suggestions ready")}>Suggest titles, description and tags</Busy>}
          {seo && (
            <details>
              <summary className="small muted">Where the suggestions came from{seo.confidence ? ` · ${seo.confidence.band} confidence (${seo.confidence.score}/100)` : ""}</summary>
              <div className="small" style={{ display: "grid", gap: 4, marginTop: 6 }}>
                {(seo.confidence?.reasons || []).length > 0 && <span className="muted">{seo.confidence.reasons.join(" · ")}</span>}
                {seo.detected_from && <span>Detected: {seo.detected_from.series} ch.{seo.detected_from.chapter} · {seo.detected_from.genre} · characters: {(seo.detected_from.characters || []).join(", ") || "—"}</span>}
                {seo.sources?.channel && <span>Channel: {seo.sources.channel.label} — {seo.sources.channel.detail}{seo.sources.channel.error ? ` (${seo.sources.channel.error})` : ""}</span>}
                {seo.sources?.research && <span>YouTube search: {seo.sources.research.detail}{seo.sources.research.query ? ` for “${seo.sources.research.query}”` : ""}{seo.sources.research.error ? ` (${seo.sources.research.error})` : ""}</span>}
                {(seo.sources?.research?.top || []).slice(0, 5).map((v: any, i: number) => (
                  <a key={i} href={v.url} target="_blank" rel="noreferrer">↳ {v.title} · {(v.views || 0).toLocaleString()} views</a>
                ))}
                {(seo.sanitized || []).length > 0 && <span className="muted">Removed before you saw it: {seo.sanitized.join(", ")}</span>}
              </div>
            </details>
          )}
          {seo && <div className="row">
            <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/seo/generate", { project: id, name }); pub.reload(); }, "New suggestions")}>↻ New suggestions</Busy>
            <Busy className="sm ghost" title="re-read the channel's latest uploads first" onClick={() => act(async () => { await api("/api/seo/generate", { project: id, name, refresh_style: true }); pub.reload(); }, "New suggestions with a fresh look at the channel")}>↻ Re-read the channel too</Busy>
          </div>}
          <div className="grid g3">
            <label className="field">Category<select id="cat" value={md.category_id} onChange={(e) => save({ category_id: e.target.value }, "Saved")}>
              {Object.entries(p.categories || {}).map(([k, v]: any) => <option key={k} value={k}>{v}</option>)}</select></label>
            <label className="field">Privacy<select id="priv" value={md.privacy} onChange={(e) => save({ privacy: e.target.value }, "Saved")}>
              {(p.privacy_options || []).map((x: string) => <option key={x} value={x}>{x[0].toUpperCase() + x.slice(1)}</option>)}</select></label>
            <div className="field">Post to
              {accounts.map((a: any) => (
                <label key={a.account_id} className="check"><input type="checkbox" checked={(md.targets || []).includes(a.account_id)}
                  onChange={(e) => save({ targets: e.target.checked ? [...(md.targets || []), a.account_id] : (md.targets || []).filter((t: string) => t !== a.account_id) }, "Saved")} />
                  {a.username || a.account_id}</label>
              ))}
            </div>
          </div>
          <div className="row small">
            <label className="check"><input type="checkbox" checked={!!md.made_for_kids} onChange={(e) => save({ made_for_kids: e.target.checked }, "Saved")} /> Made for kids</label>
            <label className="check"><input type="checkbox" checked={!!md.synthetic_disclosure} onChange={(e) => save({ synthetic_disclosure: e.target.checked }, "Saved")} /> Uses a synthetic voice (YouTube disclosure)</label>
          </div>
          <p className="small muted">Defaults for channels and privacy come from Settings. Changes here apply to this video only.</p>
        </Card>
        {!draft && <Card title="Thumbnail" right={<span className="row">
          <Busy className="sm" onClick={() => act(async () => { await api("/api/thumbcopilot/generate", { project: id, name }); pub.reload(); setCb(Date.now()); }, "New options")}>↻ New options</Busy>
          <label className="btn sm">Upload your own<input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
            const f = e.target.files?.[0]; if (!f) return;
            const r = await fetch(`/api/thumbnail?${q}`, { method: "POST", body: f, headers: { "Content-Type": f.type } });
            if (r.ok) { toast("Thumbnail uploaded"); pub.reload(); setCb(Date.now()); } else toast(await r.text(), true);
          }} /></label></span>}>
          <div onDragOver={(e) => e.preventDefault()} onDrop={async (e) => {
              e.preventDefault(); const f = e.dataTransfer.files?.[0]; if (!f) return;
              const r = await fetch(`/api/thumbnail?${q}`, { method: "POST", body: f, headers: { "Content-Type": f.type } });
              if (r.ok) { toast("Thumbnail uploaded"); pub.reload(); setCb(Date.now()); } else toast(await r.text(), true);
            }} style={{ border: "1px dashed var(--border)", borderRadius: 8, padding: 10 }}>
            {p.thumbnail?.width ? (
              <div style={{ display: "grid", gap: 8 }}><img src={`/thumbnail?${q}&cb=${cb}`} alt="Current thumbnail" style={{ width: "100%", maxWidth: 640, aspectRatio: "16/9", objectFit: "cover", borderRadius: 8, border: "2px solid var(--accent)" }} />
                <div className="row">
                  <span className="small muted">Current thumbnail · {p.thumbnail.width}×{p.thumbnail.height} · sent with the video{(p.thumbnail.advisories || []).length ? ` · ${p.thumbnail.advisories.join("; ")}` : ""}</span>
                  <ConfirmButton className="sm danger" confirm="Remove it?" onConfirm={() => act(async () => { await api("/api/thumbnail/delete", { project: id, name }); pub.reload(); setCb(Date.now()); }, "Thumbnail removed")}>Remove</ConfirmButton>
                </div></div>
            ) : <p className="small muted">{preparing ? "Making thumbnail options…" : "No thumbnail yet. Pick one below, upload one, or drop an image here."}</p>}
            <span className="small faint">Drop an image here to use it (JPG or PNG, at most 2 MB, at least 640 px wide).</span>
          </div>
          <div className="row small">
            <span className="muted">Series look: {tc.style_approved ? "locked for every chapter" : "draft"}</span>
            <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/thumbcopilot/generate", { project: id, name, new_style: true }); pub.reload(); setCb(Date.now()); }, "A new series look")}>New series look</Busy>
            {!tc.style_approved && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/thumbcopilot/style/approve", { project: id, name }); pub.reload(); }, "Series look locked")}>Lock this look for the series</Busy>}
          </div>
          {concepts.length === 0 ? <Empty>{preparing ? "Making options from the chapter’s pictures…" : "No options yet — press New options."}</Empty> : (
            <div className="thumbs">
              {concepts.map((c: any) => (
                <div key={c.id} className={`tc ${chosen === c.id ? "on" : ""}`}>
                  <img src={`/thumbconcept?${q}&concept_id=${enc(c.id)}&cb=${(tc.concepts?.generated_at || 0)}-${cb}`} alt={c.name} loading="lazy" />
                  <div className="m">
                    <b>{c.name}{c.recommended ? " · recommended" : ""}{chosen === c.id ? " · in use" : ""}</b>
                    <span className="muted">{c.overlay_text ? `Text: ${c.overlay_text}` : "No text"}</span>
                    <div className="row">
                      <Busy className={`sm ${c.recommended ? "primary" : ""}`} onClick={() => act(async () => { await api("/api/thumbcopilot/apply", { project: id, name, concept_id: c.id }); pub.reload(); setCb(Date.now()); }, "Thumbnail set")}>Use</Busy>
                      <Busy className="sm ghost" title="use it and make it the look for every later chapter of this series"
                        onClick={() => act(async () => { await api("/api/thumbcopilot/apply", { project: id, name, concept_id: c.id, set_series_default: true }); pub.reload(); setCb(Date.now()); }, "Thumbnail set as the series look")}>Use for the series</Busy>
                    </div>
                  </div>
                </div>
              ))}
            </div>
          )}
        </Card>}
      </div>
    </div>
  );
}
