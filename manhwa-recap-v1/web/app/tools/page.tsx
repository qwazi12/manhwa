"use client";
import Link from "next/link";
import { useEffect, useRef, useState } from "react";
import { api, useApi } from "@/lib/api";
import { money } from "@/lib/fmt";
import { Busy, Card, ConfirmButton, Empty, PageHead, Pill, Tabs, useAct } from "@/components/ui";

type T = "split" | "lab";

/** Developer tools: preview the splitter on any chapter (free), and build a
 *  chapter with the experimental Claude pipeline (costs Claude credit). */
export default function Tools() {
  const [tab, setTab] = useState<T>("split");
  useEffect(() => { try { const t = new URLSearchParams(location.search).get("tab") as T; if (t) setTab(t); } catch {} }, []);
  return (
    <>
      <PageHead title="Tools" sub="Testing tools. Nothing here changes your chapters." />
      <Tabs<T> value={tab} onChange={setTab} tabs={[["split", "Split lab"], ["lab", "Claude lab"]]} />
      {tab === "split" ? <SplitLab /> : <ClaudeLab />}
    </>
  );
}

function useJob(onEnd: () => void) {
  const [job, setJob] = useState<any>(null);
  const t = useRef<any>(null);
  useEffect(() => () => clearInterval(t.current), []);
  function watch(id: string) {
    clearInterval(t.current);
    t.current = setInterval(async () => {
      try {
        const s = await api(`/api/jobs/${id}`);
        setJob({ id, ...s });
        if (["done", "error", "cancelled"].includes(s.status)) { clearInterval(t.current); onEnd(); }
      } catch {}
    }, 2000);
  }
  return { job, watch };
}

function SplitLab() {
  const runs = useApi<any>("/api/split/runs");
  const chapters = useApi<any>("/api/chapters");
  const act = useAct();
  const [url, setUrl] = useState("");
  const [proj, setProj] = useState("");
  const [blur, setBlur] = useState(false);
  const [cur, setCur] = useState<any>(null);
  const [tall, setTall] = useState(false);
  const { job, watch } = useJob(() => runs.reload());
  async function show(slug: string) { if (slug) setCur(await act(() => api(`/api/split/run/${encodeURIComponent(slug)}`))); }
  useEffect(() => { if (!cur && runs.data?.runs?.length) show(runs.data.runs[0].slug); }, [runs.data]);
  useEffect(() => { if (job?.status === "done" && job.slug) show(job.slug); }, [job?.status]);
  const list = (cur?.panel_list || []).filter((p: any) => !tall || p.ar > 3);
  return (
    <div className="grid">
      <Card title="Preview the splitter (free)">
        <div className="grid g2" style={{ gap: 10 }}>
          <label className="field">Chapter link<input id="spurl" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></label>
          <label className="field">…or a chapter you already have<select id="spproj" value={proj} onChange={(e) => setProj(e.target.value)}>
            <option value="">—</option>{(chapters.data?.chapters || []).map((c: any) => <option key={c.id} value={c.id}>{c.title}</option>)}</select></label>
        </div>
        <div className="row">
          <label className="check"><input type="checkbox" checked={blur} onChange={(e) => setBlur(e.target.checked)} /> Also blur speech bubbles</label>
          <Busy className="primary" disabled={!url && !proj} onClick={() => act(async () => { const r = await api("/api/split/run", { url, project: proj, blur }); watch(r.job); }, "Splitting…")}>Split</Busy>
          {job && <span className="small muted">{job.status === "running" ? "⏳ " : ""}{job.stage || job.status}{job.total > 1 ? ` (${job.done}/${job.total})` : ""}{job.error ? ` · ${job.error}` : ""}</span>}
        </div>
      </Card>
      <Card title={cur ? `${cur.panels} panels from ${cur.pages} ${cur.format} image(s)` : "Results"} right={
        <div className="row">
          <select value={cur?.slug || ""} onChange={(e) => show(e.target.value)} style={{ width: "auto" }} aria-label="Past runs">
            <option value="">Past runs…</option>{(runs.data?.runs || []).map((m: any) => <option key={m.slug} value={m.slug}>{m.slug} · {m.panels} panels</option>)}</select>
          <label className="check small"><input type="checkbox" checked={tall} onChange={(e) => setTall(e.target.checked)} /> only very tall</label>
        </div>}>
        {!cur ? <Empty>Run a split to see the panels.</Empty> : (
          <>
            <div className="small muted">median shape {cur.median_ar} · {cur.over_3} taller than 3:1 · {(cur.stats || []).map((s: any) => `${s.page}: ${s.gaps} gaps → ${s.panels}`).join(" · ")}</div>
            <div className="thumbs" style={{ gridTemplateColumns: "repeat(auto-fill, minmax(130px, 1fr))" }}>
              {list.map((p: any) => (
                <a key={p.name} className="tc" href={`/splitimg/${cur.slug}/${p.name}`} target="_blank" rel="noreferrer" style={p.ar > 3 ? { borderColor: "var(--yellow)" } : undefined}>
                  <img src={`/splitimg/${cur.slug}/${p.name}`} alt="" loading="lazy" style={{ aspectRatio: "auto", maxHeight: 220, objectFit: "contain", background: "var(--panel2)" }} />
                  <div className="m faint">{p.w}×{p.h}</div>
                </a>
              ))}
            </div>
          </>
        )}
      </Card>
    </div>
  );
}

function ClaudeLab() {
  const { data, reload } = useApi<any>("/api/lab/projects", 15000);
  const act = useAct();
  const [url, setUrl] = useState("");
  const [splitter, setSplitter] = useState("yolo");
  const [fresh, setFresh] = useState(false);
  const { job, watch } = useJob(reload);
  return (
    <div className="grid">
      <Card title="Build a chapter with Claude (experimental)">
        {data && !data.key_configured && <div className="banner warn">No Claude key on the server (ANTHROPIC_API_KEY).</div>}
        <p className="small muted">Claude cuts or reads the panels, writes the script and plans the shots. It makes its own chapter beside your normal one, so you can compare them.</p>
        <div className="grid g2" style={{ gap: 10 }}>
          <label className="field">Chapter link<input id="laburl" value={url} onChange={(e) => setUrl(e.target.value)} placeholder="https://…" /></label>
          <label className="field">Panels cut by<select id="labsplit" value={splitter} onChange={(e) => setSplitter(e.target.value)}>
            {(data?.splitters || ["yolo", "claude"]).map((s: string) => <option key={s} value={s}>{s === "claude" ? "Claude" : "YOLO (standard)"}</option>)}</select></label>
        </div>
        <div className="row">
          <label className="check"><input type="checkbox" checked={fresh} onChange={(e) => setFresh(e.target.checked)} /> Start over (ignore a previous build)</label>
          <ConfirmButton className="primary" disabled={!/^https?:\/\//.test(url)} confirm="Spend Claude credit and build it?"
            onConfirm={() => act(async () => { const r = await api("/api/lab/run", { url, splitter, fresh }); watch(r.job); }, "Building — see the jobs bar")}>Build</ConfirmButton>
          {job && <span className="small muted">{job.stage || job.status}{job.error ? ` · ${job.error}` : ""}</span>}
        </div>
      </Card>
      <Card title="Lab chapters" pad={false}>
        <div className="list">
          {!data ? <Empty>Loading…</Empty> : data.projects.length === 0 ? <Empty>No lab chapters yet.</Empty> : data.projects.map((p: any) => (
            <div className="it" key={p.project}>
              <div style={{ display: "grid", gap: 2 }}>
                <b>{p.title}</b>
                <span className="small muted">cut by {p.splitter} · {p.panels ?? "?"} panels · {p.segments} segments · {money(p.cost_usd)}{p.error ? ` · ${p.error}` : ""}</span>
              </div>
              <div className="row">
                <Pill tone={p.ready ? "ok" : p.status === "error" ? "bad" : "warn"}>{p.ready ? "ready" : p.timeline_errors ? `${p.timeline_errors} timing errors` : p.status || "building"}</Pill>
                <Report project={p.project} />
                {p.segments > 0 && <Link className="btn sm" href={`/chapter/${p.project}`}>Open →</Link>}
              </div>
            </div>
          ))}
        </div>
      </Card>
    </div>
  );
}

function Report({ project }: { project: string }) {
  const [r, setR] = useState<any>(null);
  const [open, setOpen] = useState(false);
  const act = useAct();
  return (
    <>
      <button className="sm ghost" onClick={async () => { setOpen(!open); if (!r) setR(await act(() => api(`/api/lab/report?project=${encodeURIComponent(project)}`))); }}>Report</button>
      {open && r && (
        <div role="dialog" aria-label="Lab report" style={{ position: "fixed", inset: 0, zIndex: 80, background: "rgba(0,0,0,.55)", display: "grid", placeItems: "center", padding: 16 }}
          onClick={(e) => { if (e.target === e.currentTarget) setOpen(false); }}>
          <div className="card" style={{ width: "min(900px, 100%)", maxHeight: "85vh", overflowY: "auto" }}>
            <div className="hd"><h2>Lab report · {project}</h2><button className="sm" onClick={() => setOpen(false)}>Close</button></div>
            <div className="bd">
              {Object.entries(r).map(([k, v]: any) => (
                <div key={k} className="kv"><span>{k.replace(/_/g, " ")}</span>
                  <span className="small" style={{ textAlign: "right", whiteSpace: "pre-wrap" }}>{typeof v === "object" ? JSON.stringify(v, null, 1).slice(0, 600) : String(v)}</span></div>
              ))}
            </div>
          </div>
        </div>
      )}
    </>
  );
}
