"use client";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { enc, mmss, when } from "@/lib/fmt";
import { Busy, Card, ConfirmButton, Empty, Pill, StatusPill, Status, useAct, useToast } from "@/components/ui";

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
    <div className="grid">
      {draft && <div className="banner info">Preparing the details before the render. Whatever you set here is used for the video when it’s made. Thumbnails come after the render, because they’re built from the video’s pictures.</div>}
      {!draft && <Card title="Watch" right={<span className="small muted">{rv.data.stat?.size_mb} MB · {name}</span>}>
        <video controls preload="metadata" src={`/export/${enc(name)}?project=${enc(id)}`} />
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
        </div>
      </Card>}

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
                  <span className="small muted">{t.why}{t.score ? ` · score ${t.score.total}/100` : ""}</span>
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
        {!seo && <Busy className="sm" onClick={() => act(async () => { await api("/api/seo/generate", { project: id, name }); pub.reload(); }, "Suggestions ready")}>Suggest titles, description and tags</Busy>}
        {seo && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/seo/generate", { project: id, name }); pub.reload(); }, "New suggestions")}>↻ New suggestions</Busy>}
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

      {!draft && <><Card title="Thumbnail" right={<span className="row">
        <Busy className="sm" onClick={() => act(async () => { await api("/api/thumbcopilot/generate", { project: id, name }); pub.reload(); setCb(Date.now()); }, "New options")}>↻ New options</Busy>
        <label className="btn sm">Upload your own<input type="file" accept="image/png,image/jpeg,image/webp" hidden onChange={async (e) => {
          const f = e.target.files?.[0]; if (!f) return;
          const r = await fetch(`/api/thumbnail?${q}`, { method: "POST", body: f, headers: { "Content-Type": f.type } });
          if (r.ok) { toast("Thumbnail uploaded"); pub.reload(); setCb(Date.now()); } else toast(await r.text(), true);
        }} /></label></span>}>
        {p.thumbnail ? (
          <div className="row"><img src={`/thumbnail?${q}&cb=${cb}`} alt="Current thumbnail" style={{ width: 240, aspectRatio: "16/9", objectFit: "cover", borderRadius: 8 }} />
            <span className="small muted">Current thumbnail · {p.thumbnail.width}×{p.thumbnail.height} · sent with the video</span></div>
        ) : <p className="small muted">No thumbnail yet. Pick one below or upload your own.</p>}
        {concepts.length === 0 ? <Empty>No options yet — press New options.</Empty> : (
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
      </Card>

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
            {rec?.status === "in_progress" && <div className="banner info">Posting: {rec.stage}</div>}
            {rec?.status === "failed" && <div className="banner bad">Last try failed: {rec.error}</div>}
            <ConfirmButton className="primary" disabled={(p.readiness?.blockers || []).length > 0 || rec?.status === "in_progress"}
              confirm={`Post now as ${md.privacy}? It can't be taken back.`}
              onConfirm={() => act(async () => { await api("/api/publishing/publish", { project: id, name }); pst.reload(); onChange?.(); }, "Posting")}>Post now</ConfirmButton>
          </>
        )}
      </Card>
      <p className="small faint">Runtime {mmss(rv.data.qc?.runtime_s)} · {rv.data.qc?.segments_in_video} segments · matched {rv.data.qc?.match_method}</p>
      </>}
    </div>
  );
}
