"""D1 (rebuilt to the APPROVED combined-table template, 2026-07-19): the
storyboard shows EVERY extracted panel — not just timeline segments.

Template of record: ~/dev/dungeon-odyssey-review/full/review_table_combined.html
(user: "i want the system to give work like this ... even the template").

Columns: # (id+dims) | Panel image | System OCR (full) | System description
(full) | Script placement (blue = carries narration unit ¶N / yellow =
folded into ¶N, story told while another panel holds the screen / red =
LEFT OUT with the junk filter's reason) | On-screen timing & motion (every
segment that shows this panel: window, hold, motion, beats, badges, and the
interactive controls). Header: full stats + usage + project approve gate.

Server-side rendered from the active project's own artifacts:
descriptions.json (all panels), script.json (provenance units),
segments.json (timeline), review.json (statuses), project.json (meta).
"""
import html
import json
import os
import re


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


def build_storyboard_html(pdir, matcher, review, usage_summary, approved):
    descs = _load(os.path.join(pdir, "descriptions.json"), [])
    descs.sort(key=lambda r: _natural(r["panel_id"]))
    segs = _load(os.path.join(pdir, "segments.json"), [])
    scenes = _load(os.path.join(pdir, "script.json"), [])
    meta = _load(os.path.join(pdir, "project.json"), {})

    seg_by_panel = {}
    for s in segs:
        seg_by_panel.setdefault(s["panel_id"], []).append(s)
    unit_of = {}
    for sc in scenes:
        for pid in sc.get("panel_ids", []):
            unit_of[pid] = (sc["scene_id"], sc.get("text", ""))

    total = (segs[-1]["start"] + segs[-1]["dur"]) if segs else 0
    holds = sum(1 for s in segs if s["dur"] > 12)
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

        # ---- script placement cell (blue / yellow / red / grey) ----------
        if on_screen:
            uid = unit_of.get(pid, (None, None))[0]
            label = f'<b class="ln">¶{uid}</b> ' if uid is not None else ""
            first = seg_by_panel[pid][0]
            btxt = " ".join(b["text"] for b in first["beats"])[:300]
            script_cell = label + html.escape(btxt) + ("…" if len(btxt) == 300 else "")
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

        # ---- timing cell: every segment showing this panel + controls ----
        tcells = []
        for s in seg_by_panel.get(pid, []):
            si = s["seg_index"]
            if s.get("crop_bbox_norm"):
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
            st = review.get(str(si), {}).get("status", "pending")
            if st == "approved":
                badges.append('<span class="b ok">✅ approved</span>')
            elif st == "rejected":
                badges.append('<span class="b user">🗑 rejected</span>')
            beats = "".join(
                f'<div class="beat"><span class="bt">[{b["start"]-s["start"]:.1f}s]</span> '
                f'{html.escape(b["text"][:160])}{"…" if len(b["text"]) > 160 else ""}</div>'
                for b in s["beats"])
            tcells.append(
                f'<div class="segblock"><b>seg #{si}</b> · {_mmss(s["start"])}→'
                f'{_mmss(s["start"]+s["dur"])} ({s["dur"]:.1f}s) {" ".join(badges)}<br>'
                f'<span class="mo">cut + 0.4s fade-in · {motion} · hard cut out</span>'
                f'{beats}'
                f'<div class="acts">'
                f'<button onclick="swapPanel({si})">🔄 swap panel</button>'
                f'<button onclick="editNarr({si})">✏️ edit narration</button>'
                f'<button onclick="setStatus({si},\'approved\')">✅</button>'
                f'<button onclick="setStatus({si},\'rejected\')">🗑</button>'
                f'</div></div>')
        timing_cell = "".join(tcells) or '<i class="off">— not on video timeline —</i>'

        rows.append(f"""<tr class="{cls}">
<td class="n">{i}<br><span class="pid">{pid}</span><br><span class="dim">{w}&times;{h} (AR {ar:.1f})</span></td>
<td class="img"><a href="/panelimg/{pid}" target="_blank"><img src="/panelimg/{pid}" loading="lazy"></a></td>
<td class="ocr">{ocr}</td><td class="vis">{vis}</td>
<td class="script">{script_cell}</td>
<td class="timing">{timing_cell}</td></tr>""")

    title = f"{meta.get('series','?')} Ch.{meta.get('chapter','?')}"
    u = usage_summary or {}
    mm = meta.get("match_method", "")
    return f"""<!doctype html><html><head><meta charset="utf-8">
<title>{html.escape(title)} — storyboard: story + render plan</title>
<style>
body {{ font-family: -apple-system, Helvetica, sans-serif; margin: 0; background:#fafafa; color:#1a1a1a; }}
header {{ position: sticky; top:0; z-index:5; background:#161616; color:#fff; padding:10px 18px; display:flex; gap:16px; align-items:center; flex-wrap:wrap; }}
header .stat b {{ display:block; font-size:15px; color:#fff; }} header .stat {{ font-size:11px; color:#bbb; }}
.usage {{ font-size:11px; color:#9ad27d; }}
#approveBtn {{ margin-left:auto; background:#8d6e63; border:0; color:#fff; padding:10px 16px; border-radius:6px; font-weight:700; cursor:pointer; }}
#approveBtn.on {{ background:#2e7d32; }}
.wrap {{ padding: 14px 18px; }}
h1 {{ font-size: 19px; margin: 8px 0; }} p.meta {{ color:#555; max-width: 1200px; font-size: 13px; }}
table {{ border-collapse: collapse; width: 100%; }}
th, td {{ border: 1px solid #ddd; padding: 8px; vertical-align: top; text-align: left; }}
th {{ background: #222; color: #fff; position: sticky; top: 58px; z-index: 2; }}
td.n {{ width: 44px; font-weight: 700; }} .pid {{ font-weight:400; font-size:10px; color:#666; word-break:break-all; }}
.dim {{ font-size:10px; color:#999; }}
td.img {{ width: 190px; }} td.img img {{ max-width: 180px; max-height: 320px; object-fit: contain; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,.25); }}
td.ocr {{ width: 12%; font-size: 11px; color:#444; }}
td.vis {{ width: 17%; font-size: 12px; }}
td.script {{ width: 22%; font-size: 12px; }}
td.timing {{ width: 24%; font-size: 12px; }}
tr.sa td.script {{ background:#f2f7ff; }} .ln {{ color:#1552b8; }}
tr.fold td.script {{ background:#fffbe8; color:#7a6200; }} .unittxt {{ color:#7a6200; font-size:11px; margin-top:4px; }}
tr.omit td.script {{ background:#fbeeee; color:#8a2f2f; }}
tr.gray td.script {{ background:#f0f0f0; color:#777; }}
.segblock {{ background:#f4f9f4; border:1px solid #dbe8db; border-radius:6px; padding:6px; margin-bottom:6px; }}
.mo {{ color:#557; font-size:11px; }} .beat {{ margin-top:4px; }} .bt {{ color:#1552b8; font-size:10px; font-weight:600; }}
.b {{ font-size:10px; padding:1px 6px; border-radius:8px; }}
.b.warn {{ background:#ffe6cc; color:#8a4b00; }} .b.tall {{ background:#e8e0ff; color:#4b2fa0; }}
.b.user {{ background:#ffd9d9; color:#a01f1f; font-weight:700; }} .b.ok {{ background:#dcf0dc; color:#1d5e1d; }}
.off {{ color:#999; }}
.acts {{ margin-top:6px; display:flex; gap:5px; flex-wrap:wrap; }}
.acts button {{ font-size:11px; padding:4px 7px; border:1px solid #bbb; background:#fff; border-radius:5px; cursor:pointer; }}
.acts button:hover {{ background:#eee; }}
#cands {{ position:fixed; inset:0; background:rgba(0,0,0,.65); display:none; overflow:auto; padding:30px; z-index:10; }}
#cands .inner {{ background:#fff; border-radius:10px; padding:16px; max-width:1100px; margin:0 auto; }}
#cands img {{ max-width:150px; max-height:240px; margin:6px; cursor:pointer; border:3px solid transparent; border-radius:4px; }}
#cands img:hover {{ border-color:#1552b8; }}
dialog {{ border:0; border-radius:10px; padding:18px; width:640px; box-shadow:0 20px 60px rgba(0,0,0,.4); }}
textarea {{ width:100%; min-height:110px; font:13px/1.5 -apple-system; }}
</style></head><body>
<header>
  <div class="stat"><b>{html.escape(title)}</b>storyboard</div>
  <div class="stat"><b>{len(descs)}</b>panels extracted</div>
  <div class="stat"><b>{len(segs)}</b>segments</div>
  <div class="stat"><b>{_mmss(total)}</b>runtime</div>
  <div class="stat"><b>{holds}</b>holds &gt;12s</div>
  <div class="stat"><b>{n_approved}</b>approved</div>
  <div class="stat"><b>{html.escape(mm) or "?"}</b>match method</div>
  <div class="usage">today: {u.get("gemini_calls", 0)} gemini · {u.get("tts_chars", 0)} tts chars · ~${u.get("est_cost_usd", 0):.2f}</div>
  <button id="approveBtn" class="{'on' if approved else ''}" onclick="toggleApproval()">
    {'✔ PROJECT APPROVED — renders unlocked' if approved else 'APPROVE PROJECT FOR RENDER'}</button>
</header>
<div class="wrap">
<h1>{html.escape(title)} — combined: all {len(descs)} panels · story placement · render timing ({len(segs)} segments, {_mmss(total)})</h1>
<p class="meta">Left half: system OCR/description and where each extracted panel lands in the script
(<b style="color:#1552b8">blue</b> carries narration unit ¶N on screen · <b style="color:#7a6200">yellow</b> folded — its
story is told in ¶N while another panel holds the screen · <b style="color:#8a2f2f">red</b> LEFT OUT, with the junk
filter's reason). Right column: the renderer's real timeline — segment #, on-screen window, hold length, motion, the
narration beats heard over it, and live controls. Badges: ⚠ hold &gt;12s · 📜 tall strip (aspect ≥3 → scroll-pan) ·
✅/🗑 review status. Approving the project unlocks bulk rendering.</p>
<table>
<tr><th>#</th><th>Panel</th><th>System OCR</th><th>System description</th><th>Script placement</th><th>On-screen timing &amp; motion</th></tr>
{''.join(rows)}
</table></div>
<div id="cands" onclick="this.style.display='none'"><div class="inner" onclick="event.stopPropagation()"><h3>Pick replacement panel</h3><div id="candList"></div></div></div>
<dialog id="editDlg"><h3>Edit narration (re-TTS on save)</h3><textarea id="editTxt"></textarea><p><button onclick="saveEdit()">Save</button> <button onclick="editDlg.close()">Cancel</button></p></dialog>
<script>
let editing = null;
const APPROVED = {str(bool(approved)).lower()};
async function j(u, opt) {{ const r = await fetch(u, opt); if (!r.ok) throw new Error(await r.text()); return r.json(); }}
async function swapPanel(i) {{
  const c = await j(`/api/segments/${{i}}/candidates`);
  const list = document.getElementById('candList');
  list.innerHTML = '';
  for (const p of (c.candidates || [])) {{
    const im = document.createElement('img');
    im.src = `/panelimg/${{encodeURIComponent(p.panel_id)}}`; im.title = p.panel_id;
    im.onclick = async () => {{
      await j(`/api/segments/${{i}}/panel`, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{panel_id: p.panel_id}})}});
      location.reload();
    }};
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
  await j(`/api/segments/${{editing}}/narration`, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{text: txt}})}});
  location.reload();
}}
async function setStatus(i, st) {{
  await j(`/api/segments/${{i}}/status`, {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{status: st, note: ''}})}});
  location.reload();
}}
async function toggleApproval() {{
  await j('/api/storyboard/approve', {{method:'POST', headers:{{'Content-Type':'application/json'}}, body: JSON.stringify({{approved: !APPROVED}})}});
  location.reload();
}}
</script></body></html>"""
