"""Storyboard v2 — THE main review surface (approved combined-table template
+ editor controls + legacy sidebar, per the P1-P13 plan, 2026-07-19).

Every extracted panel in reading order with: OCR, description, script
placement (blue on-screen / yellow folded / red left-out+reason), the real
render timeline, and DIRECT editing: include/exclude checkboxes, per-segment
duration + boundary control, drag-reorder, add narration line, edit + re-TTS,
swap panel, approve/reject. Sidebar ports the legacy UI's Ingest / Logs /
Projects (grouped by series) pages. Cost header is Eastern Time + all-time.
"""
import html
import json
import os
import re
from datetime import datetime


def _natural(pid):
    return [int(t) if t.isdigit() else t for t in re.split(r"(\d+)", pid)]


def _mmss(t):
    return f"{int(t)//60}:{int(t)%60:02d}"


def _load(path, default):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError):
        return default


def _coverage_stat(sc):
    """Header chip for split art-coverage (S2). Amber warning when any page
    lost >15% of its art; green when the whole chapter is fully cropped."""
    if not sc:
        return '<div class="stat"><b>?</b>split coverage</div>'
    bad = sc.get("pages_below_85", 0)
    color = "#e5a13a" if bad else "#9ad27d"
    warn = f' ⚠ {bad} page(s) &lt;85% (worst: {html.escape(str(sc.get("worst_page","")))})' if bad else ""
    return (f'<div class="stat"><b style="color:{color}">'
            f'{sc.get("min", 0):.0%} min / {sc.get("mean", 0):.0%} mean</b>'
            f'split coverage{warn}</div>')


def _et_label():
    try:
        from zoneinfo import ZoneInfo
        return datetime.now(ZoneInfo("America/New_York")).strftime("%a %b %d, %-I:%M %p ET")
    except Exception:
        return "ET n/a"


def build_storyboard_html(pdir, matcher, review, usage_summary, approved):
    descs = _load(os.path.join(pdir, "descriptions.json"), [])
    descs.sort(key=lambda r: _natural(r["panel_id"]))
    segs = _load(os.path.join(pdir, "segments.json"), [])
    scenes = _load(os.path.join(pdir, "script.json"), [])
    meta = _load(os.path.join(pdir, "project.json"), {})

    seg_by_panel, pos_of = {}, {}
    for pos, s in enumerate(segs):
        seg_by_panel.setdefault(s["panel_id"], []).append(s)
        pos_of[s["seg_index"]] = pos
    unit_of = {}
    for sc in scenes:
        for pid in sc.get("panel_ids", []):
            unit_of[pid] = (sc["scene_id"], sc.get("text", ""))

    total = (segs[-1]["start"] + segs[-1]["dur"]) if segs else 0
    holds = sum(1 for s in segs if s["dur"] > 12)
    n_included = sum(1 for s in segs if s.get("user_included"))
    n_segs = len(segs)
    n_approved = sum(1 for s in segs
                     if review.get(str(s["seg_index"]), {}).get("status") == "approved")

    rows = []
    for i, d in enumerate(descs, 1):
        pid = d["panel_id"]
        w, h = d.get("width") or 0, d.get("height") or 0
        ar = h / max(w, 1)
        ocr = html.escape((d.get("ocr_text") or "").strip()) or "<i>none</i>"
        vis = html.escape((d.get("visual_description") or "").strip())
        on_screen = pid in seg_by_panel
        reason = matcher.junk_reason(d)
        pid_js = pid.replace("'", "\\'")

        # ---- script placement cell ----------------------------------------
        if on_screen:
            uid = unit_of.get(pid, (None, None))[0]
            label = f'<b class="ln">¶{uid}</b> ' if uid is not None else ""
            first = seg_by_panel[pid][0]
            btxt = " ".join(b["text"] for b in first["beats"])[:300]
            script_cell = (label + html.escape(btxt) + ("…" if len(btxt) == 300 else "")) \
                if btxt else "<i>on screen as a silent hold (no narration)</i>"
            cls = "sa"
        elif reason:
            script_cell = f"<i>LEFT OUT — {html.escape(reason)}</i>"
            cls = "omit"
        elif pid in unit_of:
            uid, utxt = unit_of[pid]
            script_cell = (f'<i>→ folded into <b class="ln">¶{uid}</b></i>'
                           f'<div class="unittxt">{html.escape(utxt[:220])}'
                           f'{"…" if len(utxt) > 220 else ""}</div>')
            cls = "fold"
        else:
            script_cell = "<i>unplaced (no provenance, no segment)</i>"
            cls = "gray"

        # ---- timing cell ---------------------------------------------------
        tcells = []
        for s in seg_by_panel.get(pid, []):
            si = s["seg_index"]
            pos = pos_of[si]
            silent = s.get("silent_hold") or not s["beats"]
            if silent:
                motion = "silent hold (no narration)"
            elif s.get("crop_bbox_norm"):
                motion = "planned sub-crop + Ken Burns"
            elif ar >= 3:
                motion = "tall strip → scroll-pan top→bottom"
            else:
                motion = "Ken Burns " + ("push-in" if si % 2 == 0 else "pull-out")
            badges = []
            if s["dur"] > 12:
                badges.append('<span class="b warn">⚠ long hold</span>')
            if ar >= 3 and not s.get("crop_bbox_norm"):
                badges.append('<span class="b tall">📜 tall strip</span>')
            if silent:
                badges.append('<span class="b sil">🔇 silent</span>')
            st = review.get(str(si), {}).get("status", "pending")
            if st == "approved":
                badges.append('<span class="b ok">✅ approved</span>')
            elif st == "rejected":
                badges.append('<span class="b user">🗑 rejected</span>')
            beats = "".join(
                f'<div class="beat"><span class="bt">[{b["start"]-s["start"]:.1f}s]</span> '
                f'{html.escape(b["text"][:160])}{"…" if len(b["text"]) > 160 else ""}'
                f'{"<span class=slice title=\'audio sliced at an image cut\'>✂</span>" if b.get("file") else ""}</div>'
                for b in s["beats"])
            not_last = pos + 1 < len(segs)
            tcells.append(f"""<div class="segblock" draggable="true" data-si="{si}" data-pos="{pos}"
  ondragstart="dragSeg(event)" ondragover="event.preventDefault();this.classList.add('over')"
  ondragleave="this.classList.remove('over')" ondrop="dropSeg(event,this)">
<span class="draghandle" title="drag to reorder">⠿</span>
<b>seg #{si}</b> · {_mmss(s["start"])}→{_mmss(s["start"]+s["dur"])} {' '.join(badges)}
<div class="timectl">
  ⏱ <input type="number" step="0.1" min="0.8" value="{s['dur']:.1f}" id="dur{si}"
     onkeydown="if(event.key==='Enter')setDur({si})"> s
  <button onclick="setDur({si})" title="set on-screen duration">set</button>
  <span class="bctl" title="move the cut between this segment and the next (audio slices if mid-sentence)">
    cut: <button onclick="nudge({si},-1)" {'disabled' if not not_last else ''}>−1s</button>
    <button onclick="nudge({si},-0.25)" {'disabled' if not not_last else ''}>−¼</button>
    <button onclick="nudge({si},0.25)" {'disabled' if not not_last else ''}>+¼</button>
    <button onclick="nudge({si},1)" {'disabled' if not not_last else ''}>+1s</button>
  </span>
</div>
<span class="mo">cut + 0.4s fade-in · {motion} · hard cut out</span>
{beats}
<div class="acts">
  <button onclick="swapPanel({si})">🔄 swap</button>
  <button onclick="editNarr({si})">✏️ edit narration</button>
  <button onclick="addLine({si})">✚ add line</button>
  <button onclick="setStatus({si},'approved')">✅</button>
  <button onclick="setStatus({si},'rejected')">🗑</button>
</div></div>""")
        timing_cell = "".join(tcells) or '<i class="off">— not on video timeline —</i>'

        # T3 (Session 23): the checkbox is the USER's inclusion decision for
        # the final video — never auto-checked. The system's proposal is the
        # timing column; the user ticks what actually renders/concats.
        if on_screen:
            row_segs = seg_by_panel.get(pid, [])
            _inc = " checked" if row_segs and all(s.get("user_included") for s in row_segs) else ""
            include_ctl = (
                f'<label class="inc" title="tick = include in the FINAL video">'
                f'<input type="checkbox"{_inc} onchange="setIncluded(\'{pid_js}\',this.checked)"></label>')
        else:
            include_ctl = (
                f'<button class="promote" title="give this panel its own slot on the timeline" '
                f'onclick="toggleInclude(\'{pid_js}\',null,{1 if cls=="omit" else 0})">➕</button>')

        rows.append(f"""<tr class="{cls}" id="row_{pid}">
<td class="n">{include_ctl}{i}<br><span class="pid">{pid}</span><br><span class="dim">{w}&times;{h} (AR {ar:.1f})</span></td>
<td class="img"><a href="/panelimg/{pid}" target="_blank"><img src="/panelimg/{pid}" loading="lazy"></a></td>
<td class="ocr">{ocr}</td><td class="vis">{vis}</td>
<td class="script">{script_cell}</td>
<td class="timing">{timing_cell}</td></tr>""")

    title = f"{meta.get('series','?')} Ch.{meta.get('chapter','?')}"
    u = usage_summary or {}
    life = u.get("lifetime", {})
    all_g = life.get("gemini_calls", 0) + u.get("gemini_calls", 0)
    all_t = life.get("tts_chars", 0) + u.get("tts_chars", 0)
    all_c = life.get("est_cost_usd", 0) + u.get("est_cost_usd", 0)
    mm = meta.get("match_method", "")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)} — storyboard: story + render plan</title>
<style>
body {{ font-family: -apple-system, Helvetica, sans-serif; margin: 0 0 0 64px; background:#fafafa; color:#1a1a1a; }}
header {{ position: sticky; top:0; z-index:5; background:#161616; color:#fff; padding:10px 18px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }}
header .stat b {{ display:block; font-size:15px; color:#fff; }} header .stat {{ font-size:11px; color:#bbb; }}
.usage {{ font-size:11px; color:#9ad27d; line-height:1.5; }}
#approveBtn {{ margin-left:auto; background:#8d6e63; border:0; color:#fff; padding:10px 16px; border-radius:6px; font-weight:700; cursor:pointer; }}
#approveBtn.on {{ background:#2e7d32; }}
/* ---- left rail (ported from legacy UI) ---- */
#rail {{ position:fixed; left:0; top:0; bottom:0; width:64px; background:#15171e; border-right:1px solid #282c38; display:flex; flex-direction:column; align-items:center; gap:6px; padding-top:12px; z-index:20; }}
.navbtn {{ width:52px; height:56px; border:0; background:transparent; border-radius:9px; display:flex; flex-direction:column; gap:4px; align-items:center; justify-content:center; color:#8b90a0; font-size:10px; cursor:pointer; }}
.navbtn .ic {{ font-size:19px; line-height:1; }}
.navbtn:hover, .navbtn.active {{ background:#1b1e27; color:#5b8cff; }}
/* ---- drawers ---- */
.drawer {{ position:fixed; left:64px; top:0; bottom:0; width:360px; background:#15171e; color:#e6e8ef; border-right:1px solid #282c38; z-index:19; padding:16px; overflow-y:auto; display:none; font-size:13px; }}
.drawer h3 {{ font-size:12px; text-transform:uppercase; letter-spacing:.6px; color:#8b90a0; margin:0 0 10px; }}
.drawer .hint {{ color:#8b90a0; font-size:11px; }}
.drawer input.field {{ width:100%; background:#1b1e27; border:1px solid #282c38; border-radius:7px; color:#e6e8ef; padding:8px; font:inherit; margin:10px 0; }}
.drawer button {{ font:inherit; cursor:pointer; color:#e6e8ef; background:#1b1e27; border:1px solid #282c38; border-radius:7px; padding:6px 10px; }}
.drawer button.primary {{ background:#5b8cff; border-color:#5b8cff; color:#fff; width:100%; }}
.drawer .section {{ border-top:1px solid #282c38; margin-top:12px; padding-top:12px; }}
.projcard {{ margin-top:8px; background:#1b1e27; border:1px solid #282c38; border-radius:8px; overflow:hidden; }}
.projcard .ph {{ font-weight:600; font-size:12px; padding:8px 12px; background:#15171e; border-bottom:1px solid #282c38; }}
.projrow {{ display:flex; justify-content:space-between; align-items:center; padding:6px 10px; font-size:12px; }}
.wrap {{ padding: 14px 18px; }}
h1 {{ font-size: 19px; margin: 8px 0; }} p.meta {{ color:#555; max-width: 1200px; font-size: 13px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; text-align: left; }}
th {{ background: #222; color: #fff; position: sticky; top: 58px; z-index: 2; }}
td.n {{ width: 54px; font-weight: 700; }} .pid {{ font-weight:400; font-size:10px; color:#666; word-break:break-all; }}
.dim {{ font-size:10px; color:#999; }}
.inc {{ display:block; margin-bottom:4px; }} .inc input {{ width:16px; height:16px; cursor:pointer; }}
td.img {{ width: 185px; }} td.img img {{ max-width: 175px; max-height: 320px; object-fit: contain; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,.25); }}
td.ocr {{ width: 11%; font-size: 11px; color:#444; }}
td.vis {{ width: 16%; font-size: 12px; }}
td.script {{ width: 21%; font-size: 12px; }}
td.timing {{ width: 26%; font-size: 12px; }}
tr.sa td.script {{ background:#f2f7ff; }} .ln {{ color:#1552b8; }}
tr.fold td.script {{ background:#fffbe8; color:#7a6200; }} .unittxt {{ color:#7a6200; font-size:11px; margin-top:4px; }}
tr.omit td.script {{ background:#fbeeee; color:#8a2f2f; }}
tr.gray td.script {{ background:#f0f0f0; color:#777; }}
.segblock {{ background:#f4f9f4; border:1px solid #dbe8db; border-radius:6px; padding:6px; margin-bottom:6px; position:relative; }}
.segblock.over {{ outline:2px dashed #5b8cff; }}
.draghandle {{ position:absolute; right:6px; top:6px; cursor:grab; color:#9ab; }}
.timectl {{ margin:5px 0; font-size:11px; display:flex; gap:6px; align-items:center; flex-wrap:wrap; }}
.timectl input {{ width:52px; font:inherit; padding:2px 4px; }}
.timectl button {{ font-size:10px; padding:2px 6px; border:1px solid #bbb; background:#fff; border-radius:4px; cursor:pointer; }}
.bctl {{ white-space:nowrap; }}
.mo {{ color:#557; font-size:11px; }} .beat {{ margin-top:4px; }} .bt {{ color:#1552b8; font-size:10px; font-weight:600; }}
.slice {{ color:#a05a00; margin-left:4px; }}
.b {{ font-size:10px; padding:1px 6px; border-radius:8px; }}
.b.warn {{ background:#ffe6cc; color:#8a4b00; }} .b.tall {{ background:#e8e0ff; color:#4b2fa0; }}
.b.user {{ background:#ffd9d9; color:#a01f1f; font-weight:700; }} .b.ok {{ background:#dcf0dc; color:#1d5e1d; }}
.b.sil {{ background:#e8e8e8; color:#555; }}
.off {{ color:#999; }}
.acts {{ margin-top:6px; display:flex; gap:5px; flex-wrap:wrap; }}
.acts button {{ font-size:11px; padding:4px 7px; border:1px solid #bbb; background:#fff; border-radius:5px; cursor:pointer; }}
.acts button:hover {{ background:#eee; }}
#cands {{ position:fixed; inset:0; background:rgba(0,0,0,.65); display:none; overflow:auto; padding:30px; z-index:30; }}
#cands .inner {{ background:#fff; border-radius:10px; padding:16px; max-width:1100px; margin:0 auto; }}
#cands img {{ max-width:150px; max-height:240px; margin:6px; cursor:pointer; border:3px solid transparent; border-radius:4px; }}
#cands img:hover {{ border-color:#1552b8; }}
dialog {{ border:0; border-radius:10px; padding:18px; width:640px; box-shadow:0 20px 60px rgba(0,0,0,.4); }}
textarea {{ width:100%; min-height:110px; font:13px/1.5 -apple-system; }}
#busy {{ position:fixed; bottom:16px; left:50%; transform:translateX(-50%); background:#161616; color:#fff; padding:8px 16px; border-radius:8px; display:none; z-index:40; font-size:12px; }}
</style></head><body>
<div id="rail">
  <button class="navbtn active" title="Storyboard" onclick="location.reload()"><span class="ic">🎬</span>Board</button>
  <button class="navbtn" data-d="ingest" onclick="toggleDrawer('ingest')"><span class="ic">🔗</span>Ingest</button>
  <button class="navbtn" data-d="projects" onclick="toggleDrawer('projects')"><span class="ic">📚</span>Projects</button>
  <button class="navbtn" data-d="logs" onclick="toggleDrawer('logs')"><span class="ic">📋</span>Logs</button>
  <a class="navbtn" href="/legacy/" title="legacy player UI"><span class="ic">🕰️</span>Legacy</a>
</div>
<div class="drawer" id="d_ingest">
  <h3>Ingest a chapter</h3>
  <div class="hint">Paste a chapter URL — it runs the whole pipeline
    (scrape → split → describe → narrate → voice → match → segment) and this
    board reloads on it when done. Clips render on demand after approval.</div>
  <input class="field" id="ingurl" placeholder="https://…/chapter/…"/>
  <label class="hint" style="display:flex;gap:6px;align-items:center;margin-bottom:8px">
    <input type="checkbox" id="ingfresh"> Fresh re-ingest (regenerate script,
    audio &amp; timeline — for re-running a chapter after a pipeline fix)</label>
  <button class="primary" onclick="runIngest()">▶ Run ingest</button>
  <div id="ingprog" style="margin-top:12px"></div>
</div>
<div class="drawer" id="d_projects">
  <h3>Projects</h3>
  <div id="projlist" class="hint">loading…</div>
</div>
<div class="drawer" id="d_logs">
  <h3>Logs</h3>
  <div class="hint">Live job history + every external API call (cost-tracked, Eastern Time). Survives restarts.</div>
  <button style="width:100%;margin:8px 0" onclick="loadLogs()">↻ Refresh</button>
  <div class="section"><h3>Ingest jobs</h3><div id="logjobs" class="hint">loading…</div></div>
  <div class="section"><h3>API usage</h3><div id="logusage" class="hint">loading…</div></div>
</div>
<header>
  <div class="stat"><b>{html.escape(title)}</b>storyboard</div>
  <div class="stat"><b>{len(descs)}</b>panels extracted</div>
  <div class="stat"><b>{len(segs)}</b>segments</div>
  <div class="stat"><b>{_mmss(total)}</b>runtime</div>
  <div class="stat"><b>{holds}</b>holds &gt;12s</div>
  <div class="stat"><b>{n_approved}</b>approved</div>
  <div class="stat"><b>{n_included}/{n_segs}</b>in final video</div>
  <button onclick="setIncludedAll(true)" class="mini">☑ all</button>
  <button onclick="setIncludedAll(false)" class="mini">☐ none</button>
  <div class="stat"><b>{html.escape(mm) or "?"}</b>match method</div>
  {_coverage_stat(meta.get("split_coverage"))}
  <div class="usage">{_et_label()} — today: {u.get("gemini_calls", 0)} gemini · {u.get("tts_chars", 0)} tts · ~${u.get("est_cost_usd", 0):.2f}<br>
  all-time: {all_g} gemini · {all_t} tts · ~${all_c:.2f}</div>
  <button id="approveBtn" class="{'on' if approved else ''}" onclick="toggleApproval()">
    {'✔ PROJECT APPROVED — renders unlocked' if approved else 'APPROVE PROJECT FOR RENDER'}</button>
</header>
<div class="wrap">
<h1>{html.escape(title)} — combined: all {len(descs)} panels · story placement · render timing ({len(segs)} segments, {_mmss(total)})</h1>
<p class="meta">Left half: system OCR/description and where each extracted panel lands in the script
(<b style="color:#1552b8">blue</b> carries narration unit ¶N on screen · <b style="color:#7a6200">yellow</b> folded — its
story is told in ¶N while another panel holds the screen · <b style="color:#8a2f2f">red</b> LEFT OUT, with the junk
filter's reason). Right column: the renderer's real timeline with LIVE EDITING — ✔ checkbox puts a panel on/off the
final video (folded panels get a slice of their unit's window; script-less panels get a silent hold), ⏱ sets a
segment's on-screen duration, "cut" buttons move the boundary between neighbours (narration audio slices seamlessly
if a cut lands mid-sentence ✂), ⠿ drag reorders, ✚ adds a new narrated line (TTS). Badges: ⚠ hold &gt;12s ·
📜 tall strip (scroll-pan) · 🔇 silent hold · ✅/🗑 review status. Approving the project unlocks bulk rendering.</p>
<table>
<tr><th>#</th><th>Panel</th><th>System OCR</th><th>System description</th><th>Script placement</th><th>On-screen timing &amp; motion</th></tr>
{''.join(rows)}
</table></div>
<div id="cands" onclick="this.style.display='none'"><div class="inner" onclick="event.stopPropagation()"><h3>Pick replacement panel</h3><div id="candList"></div></div></div>
<dialog id="editDlg"><h3>Edit narration (re-TTS on save)</h3><textarea id="editTxt"></textarea><p><button onclick="saveEdit()">Save</button> <button onclick="editDlg.close()">Cancel</button></p></dialog>
<div id="busy">working…</div>
<script>
let editing = null, dragFrom = null;
const APPROVED = {str(bool(approved)).lower()};
function busy(on, msg) {{ const b = document.getElementById('busy'); b.textContent = msg || 'working…'; b.style.display = on ? 'block' : 'none'; }}
async function j(u, opt) {{ const r = await fetch(u, opt); if (!r.ok) {{ let m = await r.text(); try {{ m = JSON.parse(m).detail || m; }} catch(e) {{}} throw new Error(m); }} return r.json(); }}
async function post(u, body, msg) {{
  busy(true, msg);
  try {{ const r = await j(u, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify(body)}}); location.reload(); return r; }}
  catch (e) {{ busy(false); alert(e.message); }}
}}
/* ---- editor ops ---- */
function toggleInclude(pid, cb, isJunk) {{
  /* promote a folded/left-out panel onto the timeline (its checkbox starts
     UNTICKED — the user still decides final-video inclusion, T3) */
  let hold = 2.5;
  if (isJunk) {{
    const v = prompt('This panel has no narration — it gets a SILENT hold (extends runtime). Seconds on screen:', '2.5');
    if (v === null) return;
    hold = parseFloat(v) || 2.5;
  }}
  post('/api/storyboard/include', {{panel_id: pid, hold}}, 'placing panel on the timeline…');
}}
function setIncluded(pid, on) {{
  post('/api/storyboard/set_included', {{panel_id: pid, included: on}},
       on ? 'adding to final video…' : 'removing from final video…');
}}
function setIncludedAll(on) {{
  post('/api/storyboard/set_included', {{all: true, included: on}}, 'updating all…');
}}
function setDur(si) {{
  const v = parseFloat(document.getElementById('dur' + si).value);
  if (!v || v <= 0) return alert('enter seconds');
  post('/api/storyboard/duration', {{seg_index: si, dur: v}}, 'retiming…');
}}
function nudge(si, delta) {{ post('/api/storyboard/boundary', {{seg_index: si, delta}}, 'moving cut…'); }}
function addLine(si) {{
  const t = prompt('New narration sentence (costs its TTS characters):');
  if (t && t.trim()) post('/api/storyboard/addline', {{seg_index: si, text: t.trim()}}, 'synthesizing…');
}}
function dragSeg(ev) {{ dragFrom = parseInt(ev.target.closest('.segblock').dataset.si); }}
function dropSeg(ev, el) {{
  el.classList.remove('over');
  const to = parseInt(el.dataset.pos);
  if (dragFrom === null || isNaN(to)) return;
  post('/api/storyboard/move', {{seg_index: dragFrom, to}}, 'reordering…');
}}
/* ---- existing controls ---- */
async function swapPanel(i) {{
  const c = await j(`/api/segments/${{i}}/candidates`);
  const list = document.getElementById('candList');
  list.innerHTML = '';
  for (const p of (c.candidates || [])) {{
    const im = document.createElement('img');
    im.src = `/panelimg/${{encodeURIComponent(p.panel_id)}}`; im.title = p.panel_id;
    im.onclick = () => post(`/api/segments/${{i}}/panel`, {{panel_id: p.panel_id}}, 'swapping…');
    list.appendChild(im);
  }}
  document.getElementById('cands').style.display = 'block';
}}
async function editNarr(i) {{
  editing = i;
  const d = await j('/api/project');
  const s = (d.segments || []).find(x => x.seg_index === i);
  document.getElementById('editTxt').value = (s.beats || []).map(b => b.text).join(' ');
  document.getElementById('editDlg').showModal();
}}
async function saveEdit() {{
  const txt = document.getElementById('editTxt').value.trim();
  document.getElementById('editDlg').close();
  const d = await j('/api/project');
  const s = (d.segments || []).find(x => x.seg_index === editing);
  const beats = (s.beats && s.beats.length) ? [{{index: s.beats[0].index, text: txt}}] : [];
  if (!beats.length) return alert('this segment has no narration line — use ✚ add line');
  await post(`/api/segments/${{editing}}/narration`, {{beats}}, 're-synthesizing…');
}}
async function setStatus(i, st) {{
  await post(`/api/segments/${{i}}/status`, {{status: st, note: ''}});
}}
async function toggleApproval() {{
  await post('/api/storyboard/approve', {{approved: !APPROVED}});
}}
/* ---- drawers: ingest / projects / logs (ported from legacy UI) ---- */
function toggleDrawer(name) {{
  for (const d of ['ingest','projects','logs']) {{
    const el = document.getElementById('d_' + d);
    const btn = document.querySelector(`.navbtn[data-d="${{d}}"]`);
    const show = d === name && el.style.display !== 'block';
    el.style.display = show ? 'block' : 'none';
    if (btn) btn.classList.toggle('active', show);
  }}
  if (name === 'projects') loadProjects();
  if (name === 'logs') loadLogs();
  if (name === 'ingest') {{ paintIngest(); if (activeJob()) startIngestPoller(); }}
}}
const ING_STAGES = ['scrape','split','describe','narrate','voice','match','segment'];
let ingestState = null, ingestPolling = false;
function activeJob() {{ return localStorage.getItem('activeIngestJob'); }}
function setActiveJob(id) {{ if (id) localStorage.setItem('activeIngestJob', id); else localStorage.removeItem('activeIngestJob'); }}
async function runIngest() {{
  const url = document.getElementById('ingurl').value.trim();
  if (!/^https?:\\/\\//.test(url)) {{ alert('Paste a full http(s) chapter URL'); return; }}
  const fresh = document.getElementById('ingfresh').checked;
  if (fresh && !confirm('Fresh re-ingest regenerates narration, TTS audio and the timeline for this chapter (cached descriptions and unchanged TTS lines are still reused). Continue?')) return;
  try {{
    const r = await j('/api/ingest', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{url, fresh}})}});
    setActiveJob(r.job); startIngestPoller();
  }} catch (e) {{ alert(e.message); }}
}}
function stageBar(cur, pct, msg, err) {{
  return `<div style="font-size:12px">${{ING_STAGES.map(s => {{
    const done = ING_STAGES.indexOf(s) < ING_STAGES.indexOf(cur), on = s === cur;
    return `<div style="display:flex;align-items:center;gap:6px;margin:2px 0;color:${{done ? '#39c07f' : on ? '#5b8cff' : '#8b90a0'}}">
      <span>${{done ? '✓' : on ? '●' : '○'}}</span>${{s}}</div>`; }}).join('')}}</div>
    <div style="height:6px;background:#282c38;border-radius:3px;margin:8px 0;overflow:hidden">
      <div style="height:100%;width:${{pct}}%;background:${{err ? '#ef5f6b' : '#5b8cff'}}"></div></div>
    <div class="hint">${{err ? ('⚠ ' + err) : (msg || '')}}</div>`;
}}
function paintIngest() {{
  const box = document.getElementById('ingprog'); if (!box || !ingestState) return;
  const s = ingestState;
  box.innerHTML = stageBar(s.stage, s.pct, s.msg, s.status === 'error' ? s.error : null);
  if (s.status === 'done' && s.project) {{
    box.innerHTML += `<button class="primary" style="margin-top:8px" onclick="activateProj('${{s.project.id}}')">Open “${{s.project.id}}” (${{s.project.n_segments}} segs)</button>`;
  }}
}}
async function startIngestPoller() {{
  if (ingestPolling) return; ingestPolling = true;
  try {{
    while (activeJob()) {{
      let s;
      try {{ s = await j('/api/ingest/status/' + activeJob()); }}
      catch (e) {{ await new Promise(r => setTimeout(r, 2500)); continue; }}
      ingestState = s; paintIngest();
      if (s.status === 'done' || s.status === 'error') {{
        setActiveJob(null);
        if (s.status === 'done' && s.project) await activateProj(s.project.id);
        break;
      }}
      await new Promise(r => setTimeout(r, 1500));
    }}
  }} finally {{ ingestPolling = false; }}
}}
if (activeJob()) startIngestPoller();   // survive reloads mid-ingest
async function loadProjects() {{
  const box = document.getElementById('projlist');
  const d = await j('/api/projects');
  let htmlOut = '';
  if ((d.in_progress || []).length) {{
    htmlOut += '<div class="hint" style="text-transform:uppercase;font-weight:600;margin:4px 0">In progress</div>' +
      d.in_progress.map(p => `<div class="projrow">⏳ ${{p.slug}} <span class="hint">${{p.stage}} · ${{p.pct}}%</span>
        <button onclick="setActiveJob('${{p.job}}');toggleDrawer('ingest');startIngestPoller()">Watch</button></div>`).join('');
  }}
  const grouped = {{}};
  (d.projects || []).forEach(p => {{
    const s = p.series || 'Other';
    (grouped[s] = grouped[s] || []).push(p);
  }});
  for (const series in grouped) {{
    htmlOut += `<div class="projcard"><div class="ph">📚 ${{series}}</div>`;
    grouped[series].forEach(p => {{
      const label = p.chapter ? ('Chapter ' + p.chapter) : p.id;
      htmlOut += `<div class="projrow"><span>${{p.active ? '▶ ' : ''}}${{label}} <span class="hint">(${{p.n_segments}} segs${{p.duration ? ' · ' + p.duration + 's' : ''}})</span></span>
        ${{p.active ? '<span class="hint">active</span>' : `<button onclick="activateProj('${{p.id}}')">Open</button>`}}</div>`;
    }});
    htmlOut += '</div>';
  }}
  box.innerHTML = htmlOut || 'No projects yet.';
}}
async function activateProj(id) {{
  await j('/api/activate', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{id}})}});
  location.reload();
}}
async function loadLogs() {{
  try {{
    const ij = await j('/api/logs/ingest');
    document.getElementById('logjobs').innerHTML = (ij.jobs || []).slice(0, 20).map(x => {{
      const col = x.status === 'done' ? '#39c07f' : x.status === 'error' ? '#ef5f6b' : '#5b8cff';
      return `<div style="border-bottom:1px solid #282c38;padding:4px 0;font-size:12px">
        <div style="color:${{col}}">${{x.status}} · ${{x.stage || ''}} ${{x.pct ? ('· ' + x.pct + '%') : ''}}</div>
        <div class="hint" style="word-break:break-all">${{(x.url || '').replace('https://','')}}</div>
        ${{x.error ? `<div style="color:#ef5f6b">⚠ ${{x.error}}</div>` : ''}}</div>`;
    }}).join('') || 'No jobs yet.';
    const uj = await j('/api/logs/usage?limit=60');
    const s = uj.summary || {{}}, life = s.lifetime || {{}};
    document.getElementById('logusage').innerHTML =
      `<div style="font-size:12px;margin-bottom:6px"><b>${{s.gemini_calls || 0}}</b> Gemini · <b>${{(s.tts_chars || 0).toLocaleString()}}</b> TTS chars ·
       est <b>$${{(s.est_cost_usd || 0).toFixed(3)}}</b> on ${{s.date || ''}} (ET)<br>
       all-time: <b>${{(life.gemini_calls || 0) + (s.gemini_calls || 0)}}</b> Gemini ·
       <b>$${{((life.est_cost_usd || 0) + (s.est_cost_usd || 0)).toFixed(2)}}</b></div>` +
      (uj.calls || []).slice(-40).reverse().map(c =>
        `<div class="hint" style="border-bottom:1px solid #282c38;padding:2px 0">
         ${{c.kind}} · ${{c.model || ''}} · ${{c.units}} ${{c.unit || ''}} · $${{(c.est_cost_usd || 0).toFixed(4)}}
         <span style="opacity:.6">${{(c.job_id || '').slice(0, 8)}}</span></div>`).join('');
  }} catch (e) {{ document.getElementById('logjobs').innerHTML = 'Failed to load logs.'; }}
}}
</script></body></html>"""
