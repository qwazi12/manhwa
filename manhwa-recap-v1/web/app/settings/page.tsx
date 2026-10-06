"use client";
import { useEffect, useState } from "react";
import { api, useApi } from "@/lib/api";
import { ago, money, when } from "@/lib/fmt";
import { Busy, Card, ConfirmButton, Empty, PageHead, Pill, useAct } from "@/components/ui";

export default function Settings() {
  const { data: d, reload } = useApi<any>("/api/settings/overview");
  if (!d) return <><PageHead title="Settings" /><Empty>Loading…</Empty></>;
  return (
    <>
      <PageHead title="Settings" sub="Everything the studio does by itself, and every default, in one place." />
      <div className="grid g2">
        <Autopilot d={d} reload={reload} />
        <Voice />
        <Channels d={d} reload={reload} />
        <Schedule d={d} reload={reload} />
        <Export d={d} reload={reload} />
        <TitleFormat d={d} reload={reload} />
        <Drive d={d} />
        <Spending d={d} />
        <Storage d={d} />
        <Connection d={d} />
        <Tools />
      </div>
    </>
  );
}

function Autopilot({ d, reload }: any) {
  const act = useAct();
  const ap = useApi<any>("/api/autopilot");
  const [perDay, setPerDay] = useState<number | "">("");
  const [budget, setBudget] = useState<number | "">("");
  useEffect(() => { if (ap.data) { setPerDay(ap.data.settings.per_day); setBudget(ap.data.settings.budget_usd); } }, [ap.data]);
  const on = d.autopilot.enabled;
  return (
    <Card title="Autopilot" right={<Pill tone={on ? "ok" : "warn"}>{on ? "● Running" : "⏸ Paused"}</Pill>}>
      <p className="small muted">Makes the next chapter of each tracked series in turn, starting at its latest {d.autopilot.window} chapters, using {d.autopilot.model} ({d.autopilot.tier}, half price).</p>
      <div className="grid" style={{ gridTemplateColumns: "1fr 1fr", gap: 10 }}>
        <label className="field">Chapters a day<input id="ap-perday" type="number" min={0} max={20} value={perDay} onChange={(e) => setPerDay(e.target.value === "" ? "" : Number(e.target.value))} /></label>
        <label className="field">Daily budget ($)<input id="ap-budget" type="number" min={0} max={100} step={0.5} value={budget} onChange={(e) => setBudget(e.target.value === "" ? "" : Number(e.target.value))} /></label>
      </div>
      <div className="row">
        <Busy className="sm primary" onClick={() => act(async () => { await api("/api/autopilot/settings", { per_day: Number(perDay), budget_usd: Number(budget) }); reload(); ap.reload(); }, "Saved")}>Save</Busy>
        <Busy className="sm ghost" title="re-read every series page now and run one autopilot check (free)" onClick={() => act(async () => { await api("/api/autopilot/check", {}); ap.reload(); }, "Checking — updates within a minute")}>↻ Check now</Busy>
        <ConfirmButton className={`sm ${on ? "danger" : ""}`} confirm={on ? "Tap to pause" : "Tap to start"}
          onConfirm={() => act(async () => { await api("/api/autopilot/settings", { enabled: !on }); reload(); }, on ? "Paused" : "Running")}>
          {on ? "Pause autopilot" : "Start autopilot"}
        </ConfirmButton>
      </div>
      {ap.data?.next && <div className="small muted">Next: {ap.data.next.series} ch.{ap.data.next.chapter} · made today {ap.data.today}/{ap.data.per_day}{ap.data.waiting ? ` · ${ap.data.waiting}` : ""}</div>}
    </Card>
  );
}

function Voice() {
  const { data: v, reload } = useApi<any>("/api/voices");
  const act = useAct();
  const [voice, setVoice] = useState("");
  const [style, setStyle] = useState("");
  const [audio, setAudio] = useState<string | null>(null);
  useEffect(() => { if (v) { setVoice(v.default.id); setStyle(v.default.style || ""); } }, [v]);
  if (!v) return <Card title="Narrator voice"><Empty>Loading…</Empty></Card>;
  const styles = [...v.styles];
  if (v.default.style && !styles.some((x: any) => x.style === v.default.style)) styles.push({ style: v.default.style, label: v.default.style });
  return (
    <Card title="Narrator voice" right={<span className="small muted">studio default</span>}>
      <label className="field">Voice<select id="voice" value={voice} onChange={(e) => setVoice(e.target.value)}>{v.voices.map((x: any) => <option key={x.id} value={x.id}>{x.label}</option>)}</select></label>
      <label className="field">Style<select id="voicestyle" value={style} onChange={(e) => setStyle(e.target.value)} disabled={voice.startsWith("chirp:")}>
        {styles.map((x: any) => <option key={x.style} value={x.style}>{x.label}</option>)}</select></label>
      <div className="row">
        <Busy className="sm" onClick={() => act(async () => { const r = await api("/api/voices/preview", { voice, style }); setAudio(r.url + "?t=" + Date.now()); })}>▶ Preview</Busy>
        <Busy className="sm primary" onClick={() => act(async () => { await api("/api/voices/default", { voice, style }); reload(); }, "Saved as the studio voice")}>Save as default</Busy>
      </div>
      {audio && <audio src={audio} controls autoPlay style={{ width: "100%" }} />}
      <p className="small muted">New chapters use it. Approving an older chapter re-records it in this voice first.</p>
    </Card>
  );
}

function Channels({ d, reload }: any) {
  const act = useAct();
  const defs = d.channels.defaults || {};
  const [targets, setTargets] = useState<string[]>(defs.targets || []);
  const [privacy, setPrivacy] = useState(defs.privacy || "private");
  return (
    <Card title="Channels & privacy" right={<span className="small muted">{d.channels.status?.detail || d.channels.status?.state}</span>}>
      <div className="grid" style={{ gap: 6 }}>
        {(d.channels.accounts || []).map((a: any) => (
          <div key={a.account_id} className="spread">
            <label className="check">
              <input type="checkbox" checked={targets.includes(a.account_id)} onChange={(e) => setTargets(e.target.checked ? [...targets, a.account_id] : targets.filter((t) => t !== a.account_id))} />
              {a.username || a.account_id} <span className="small faint">{a.account_id}{a.active ? "" : " · inactive"}</span>
            </label>
            <ConfirmButton className="sm ghost" confirm="Remove from the studio?" title="forgets it here; it stays linked in Upload-Post"
              onConfirm={() => act(async () => { await api("/api/publishing/disconnect", { account_id: a.account_id }); reload(); }, "Removed")}>Remove</ConfirmButton>
          </div>
        ))}
        <div className="row">
          <a className="btn sm" href="/api/publishing/connect?network=youtube">＋ Connect a channel</a>
          <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/publishing/refresh", {}); reload(); }, "Channel list refreshed")}>↻ Refresh the list</Busy>
        </div>
        {!d.channels.accounts?.length && <span className="muted">No connected channels.</span>}
      </div>
      <label className="field">New videos post as<select id="privacy" value={privacy} onChange={(e) => setPrivacy(e.target.value)}>
        <option value="public">Public</option><option value="unlisted">Unlisted</option><option value="private">Private</option></select></label>
      <Busy className="sm primary" onClick={() => act(async () => { await api("/api/settings", { publish: { targets, privacy } }); reload(); }, "Saved")}>Save</Busy>
      <p className="small muted">Each video can still change its own channels and privacy before it posts.</p>
    </Card>
  );
}

function Schedule({ d, reload }: any) {
  const act = useAct();
  const s = d.schedule || {};
  const [times, setTimes] = useState((s.times || []).join(", "));
  const [cap, setCap] = useState(s.per_channel_per_day || 1);
  const save = (enabled: boolean) => act(async () => {
    const t = times.split(",").map((x: string) => x.trim()).filter(Boolean).map((x: string) => (/^\d:\d\d$/.test(x) ? "0" + x : x));
    await api("/api/settings", { schedule: { enabled, times: t, per_channel_per_day: Number(cap) } });
    reload();
  }, "Saved");
  return (
    <div id="schedule">
      <Card title="Posting schedule" right={<Pill tone={s.enabled ? "ok" : "muted"}>{s.enabled ? `● On · next ${s.next || "—"}` : "Off"}</Pill>}>
        <label className="field">Post at (Eastern, 24-hour)<input id="times" value={times} onChange={(e) => setTimes(e.target.value)} placeholder="12:00, 18:00" /></label>
        <label className="field">At most, per channel per day<select id="cap" value={cap} onChange={(e) => setCap(Number(e.target.value))}>{[1, 2, 3, 4, 5].map((n) => <option key={n}>{n}</option>)}</select></label>
        <div className="row">
          <Busy className="sm" onClick={() => save(!!s.enabled)}>Save times</Busy>
          {s.enabled ? <Busy className="sm danger" onClick={() => save(false)}>Turn off</Busy> :
            <ConfirmButton className="sm primary" confirm="Posts go out by themselves — turn on?" onConfirm={() => save(true)}>Turn on</ConfirmButton>}
        </div>
        <p className="small muted">At each time, the next approved video in the Queue posts with its own channels and privacy. Posts can’t be taken back.</p>
      </Card>
    </div>
  );
}

function Export({ d, reload }: any) {
  const act = useAct();
  const [speed, setSpeed] = useState(d.export.speed);
  return (
    <Card title="Video export">
      <label className="field">Speed<select id="speed" value={speed} disabled={d.export.env_override} onChange={(e) => setSpeed(Number(e.target.value))}>
        {[1.0, 1.1, 1.25, 1.5].map((v) => <option key={v} value={v}>{v}×</option>)}</select></label>
      <Busy className="sm primary" disabled={d.export.env_override} onClick={() => act(async () => { await api("/api/settings", { export_speed: Number(speed) }); reload(); }, "Saved")}>Save</Busy>
      <p className="small muted">{d.export.env_override ? "Set by the EXPORT_SPEED variable on Railway." : "Every export is also levelled to −14 LUFS, the loudness YouTube plays at."}</p>
    </Card>
  );
}

function Drive({ d }: any) {
  const dr = d.drive;
  return (
    <Card title="Google Drive copy" right={dr ? <Pill tone={dr.ok ? "ok" : dr.configured ? "warn" : "muted"}>{dr.ok ? "Connected" : dr.configured ? "Needs attention" : "Not set up"}</Pill> : null}>
      {!dr ? <p className="small muted">Checking…</p> : (
        <>
          <div className="kv"><span>Robot</span><span className="small">{dr.robot || "—"}</span></div>
          <div className="kv"><span>Folder</span><span className="small">{dr.folder_name || dr.folder_id || "—"}</span></div>
          <div className="kv"><span>Copied</span><span>{dr.copied ?? 0} video(s)</span></div>
          {dr.error && <div className="banner warn small">{dr.error}</div>}
          <p className="small muted">After each export, the video, thumbnail and script are copied to Drive in Series / Ch N folders. Once the copy is safe, the chapter’s render clips are deleted to free disk.</p>
        </>
      )}
    </Card>
  );
}

function Spending({ d }: any) {
  return (
    <Card title="Spending">
      <div className="kv"><span>Whole site today</span><span className="num">{money(d.spending.today)} of {money(d.spending.cap)}</span></div>
      <div className="kv"><span>Autopilot today</span><span className="num">{money(d.spending.autopilot_spent)} of {money(d.spending.autopilot_budget)}</span></div>
      <div className="kv"><span>Price list</span><span>Google’s published rates, read {d.spending.prices_read}</span></div>
      <CapEditor sp={d.spending} />
      <p className="small muted">Every paid call (AI, voice, SEO) is checked against this limit before it runs; when it’s reached, work pauses until midnight ET. Autopilot also has its own budget (Autopilot card). Details are in Activity → Spend.</p>
    </Card>
  );
}

function Storage({ d }: any) {
  const st = d.storage;
  return (
    <Card title="Storage & backups">
      <div className="kv"><span>Disk</span><span className="num">{st.disk?.used_gb} of {st.disk?.total_gb} GB</span></div>
      <div className="kv"><span>Chapters</span><span>{st.n_projects}</span></div>
      <div className="kv"><span>Exports kept</span><span>{st.exports_kept_days} days (scheduled videos are kept until posted)</span></div>
      <div className="kv"><span>Posted chapters</span><span>archived, deleted {st.archive_days} days later unless kept</span></div>
      <details><summary className="small muted">Largest chapters and backup downloads</summary>
        <div className="list">{(st.projects || []).map((p: any) => (
          <div className="it" key={p.id} style={{ padding: "6px 0" }}><span>{p.name}</span><span className="row"><span className="small muted">{p.mb} MB</span><a className="btn sm" href={`/api/backup/${encodeURIComponent(p.id)}`}>Download</a></span></div>))}</div>
      </details>
    </Card>
  );
}

function Connection({ d }: any) {
  const sch = d.connection.scheduler || {};
  return (
    <Card title="Connection">
      <div className="kv"><span>Server</span><span>live · deploy <span className="mono">{d.connection.commit || "local"}</span></span></div>
      <div className="kv"><span>Background checks</span><span>{sch.running ? `running · last ${ago(sch.last_run)}` : "not running"}</span></div>
      {sch.last_error && <div className="banner warn small">{sch.last_error}</div>}
    </Card>
  );
}

function Tools() {
  return (
    <Card title="Tools">
      <div className="row">
        <a className="btn sm" href="/legacy">Legacy (the old studio)</a>
        <a className="btn sm" href="/tools?tab=split">Split lab</a>
        <a className="btn sm" href="/tools?tab=lab">Claude lab</a>
      </div>
      <p className="small muted">Everything the old pages did is in the new studio. The old studio stays under Legacy for whenever you want it.</p>
    </Card>
  );
}

/** The site's daily spend limit (owner, 2026-10-05: "allow me to increase
 *  daily spending"). Never above the Railway ceiling. */
function CapEditor({ sp }: { sp: any }) {
  const act = useAct();
  const [v, setV] = useState<number | "">(sp.cap ?? "");
  useEffect(() => { setV(sp.cap ?? ""); }, [sp.cap]);
  const left = Math.max(0, (sp.cap || 0) - (sp.today || 0));
  return (
    <div style={{ display: "grid", gap: 8, padding: "8px 0" }}>
      {(sp.limits || []).map((l: any) => (
        <div className="kv" key={l.var}><span>{l.name} today</span><span className="num">{l.used.toLocaleString()} of {l.max.toLocaleString()}{l.used >= l.max * 0.9 ? " · nearly used" : ""} <span className="faint">(Railway: {l.var})</span></span></div>
      ))}
      {sp.paused_by_limit > 0 && <div className="banner warn">{sp.paused_by_limit} chapter(s) paused by a daily limit. They resume by themselves as soon as there’s room (for example after you raise a limit) or at midnight ET.</div>}
      <div className="kv"><span>Daily limit</span><span className="num">{money(sp.cap)}{sp.set_in_app ? ` · set here ${when(sp.set_at)}` : " · the Railway value"} · {money(left)} left today</span></div>
      <div className="row">
        <label className="field" style={{ maxWidth: 200 }}>New daily limit ($, up to {money(sp.ceiling)})
          <input id="spend-cap" type="number" min={0.5} max={sp.ceiling} step={1} value={v} onChange={(e) => setV(e.target.value === "" ? "" : Number(e.target.value))} /></label>
        <ConfirmButton className="sm primary" disabled={v === "" || Number(v) === sp.cap}
          confirm={`Set the daily limit to $${v}?`}
          onConfirm={() => act(async () => { await api("/api/spend/cap", { usd: Number(v) }); location.reload(); }, "Daily limit changed")}>Save</ConfirmButton>
        {sp.set_in_app && <Busy className="sm ghost" onClick={() => act(async () => { await api("/api/spend/cap", { usd: null }); location.reload(); }, "Back to the Railway value")}>Back to {money(sp.railway_default)}</Busy>}
      </div>
      <span className="small faint">The most it can be set to ({money(sp.ceiling)}) is the Railway variable MAX_DAILY_SPEND_CEILING_USD. Undo: “Back to {money(sp.railway_default)}”.</span>
    </div>
  );
}

/** One title format for every video (owner, 2026-10-05: "we need uniformity
 *  for titles, especially since there will be multiple chapters"). */
function TitleFormat({ d, reload }: any) {
  const act = useAct();
  const [t, setT] = useState<string>(d.title_template || "{hook} | {series} Ch.{chapter}");
  const sample = t.replace("{hook}", "He Finally BREAKS Through").replace("{series}", "A Regressor's Tale of Cultivation").replace("{chapter}", "31");
  return (
    <Card title="Video titles">
      <label className="field">Title format<input id="title-format" value={t} onChange={(e) => setT(e.target.value)} /></label>
      <div className="small">Example: <b>{sample}</b></div>
      <div className="row">
        <Busy className="sm primary" disabled={t === d.title_template} onClick={() => act(async () => { await api("/api/settings", { title_template: t }); reload(); }, "Saved — new suggestions use it")}>Save</Busy>
        <button className="sm ghost" onClick={() => setT("{hook} | {series} Ch.{chapter}")}>Default</button>
      </div>
      <p className="small muted">{"{hook}"} is written by the AI for each chapter; {"{series}"} is the series’ official English title (from the web research) and {"{chapter}"} the chapter number. Every chapter of a series gets the same shape. Titles already filled in keep their text until you press ↻ New suggestions.</p>
    </Card>
  );
}
