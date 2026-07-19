"""D1: the interactive storyboard — the combined review table, served per
project straight from its own artifacts (segments + review + media + usage).

One self-contained HTML page; all data is fetched same-origin from the
existing API endpoints (the Vercel proxy injects auth), and every control
maps to an endpoint that already existed:

    swap panel      POST /api/segments/{i}/panel   (choices: /candidates)
    edit narration  POST /api/segments/{i}/narration  (re-TTS + re-render)
    approve/reject  POST /api/segments/{i}/status
    project gate    POST /api/storyboard/approve   (gates full renders)

The page is the institutionalized pre-render review gate: misassigned
panels, dead holds, and junk leakage are caught HERE, before render spend.
"""

STORYBOARD_HTML = r"""<!doctype html><html><head><meta charset="utf-8">
<title>Storyboard — pre-render review</title>
<style>
body { font-family: -apple-system, Helvetica, sans-serif; margin: 0; background:#f5f4f2; color:#1a1a1a; }
header { position: sticky; top: 0; z-index: 5; background:#161616; color:#fff; padding: 12px 20px; display:flex; gap:18px; align-items:center; flex-wrap:wrap; }
header .stat b { font-size: 15px; } header .stat { font-size: 12px; color:#bbb; }
header .stat b { color:#fff; display:block; }
#approveBtn { margin-left:auto; background:#2e7d32; border:0; color:#fff; padding:10px 18px; border-radius:6px; font-weight:700; cursor:pointer; }
#approveBtn.off { background:#8d6e63; }
table { border-collapse: collapse; width: 100%; }
th, td { border-bottom: 1px solid #ddd; padding: 10px; vertical-align: top; text-align:left; }
th { background:#222; color:#fff; position:sticky; top:64px; font-size:12px; }
td.n { width:30px; font-weight:700; }
td.tc { width:120px; font-size:13px; } .dur { color:#555; font-size:12px; }
.longhold { background:#fff3e6; border-radius:4px; padding:1px 5px; color:#b45309; font-weight:600; font-size:11px; }
td.img { width:190px; } td.img img { max-width:180px; max-height:300px; object-fit:contain; border-radius:4px; box-shadow:0 1px 4px rgba(0,0,0,.3); cursor:zoom-in; }
.pid { font-size:10px; color:#888; word-break:break-all; }
td.mo { width:16%; font-size:12px; color:#444; }
td.beats { font-size:13px; }
.beat { margin-bottom:6px; } .bt { color:#1552b8; font-size:11px; font-weight:600; }
td.act { width:150px; }
button.a { display:block; width:100%; margin-bottom:6px; padding:6px 8px; border:1px solid #bbb; background:#fff; border-radius:5px; cursor:pointer; font-size:12px; }
button.a:hover { background:#eee; }
.st-approved { border-left:4px solid #2e7d32; } .st-rejected { border-left:4px solid #c62828; opacity:.55; }
#cands { position:fixed; inset:0; background:rgba(0,0,0,.65); display:none; overflow:auto; padding:30px; z-index:10; }
#cands .inner { background:#fff; border-radius:10px; padding:18px; max-width:1100px; margin:0 auto; }
#cands img { max-width:150px; max-height:240px; margin:6px; cursor:pointer; border:3px solid transparent; border-radius:4px; }
#cands img:hover { border-color:#1552b8; }
dialog { border:0; border-radius:10px; padding:18px; width:640px; box-shadow:0 20px 60px rgba(0,0,0,.4); }
textarea { width:100%; min-height:110px; font:13px/1.5 -apple-system; }
.usage { font-size:11px; color:#9ad27d; }
</style></head><body>
<header>
  <div class="stat"><b id="hTitle">…</b>storyboard</div>
  <div class="stat"><b id="hSegs">–</b>segments</div>
  <div class="stat"><b id="hDur">–</b>runtime</div>
  <div class="stat"><b id="hHold">–</b>holds &gt;12s</div>
  <div class="stat"><b id="hAppr">–</b>approved</div>
  <div class="stat usage" id="hUsage">usage –</div>
  <button id="approveBtn" class="off" onclick="toggleProjectApproval()">APPROVE PROJECT FOR RENDER</button>
</header>
<table id="tbl"><tr><th>#</th><th>Time</th><th>Panel</th><th>Motion & transition</th><th>Narration during hold</th><th>Actions</th></tr></table>
<div id="cands" onclick="this.style.display='none'"><div class="inner" onclick="event.stopPropagation()"><h3>Pick replacement panel</h3><div id="candList"></div></div></div>
<dialog id="editDlg"><h3>Edit narration (re-TTS on save)</h3><textarea id="editTxt"></textarea><p><button onclick="saveEdit()">Save</button> <button onclick="editDlg.close()">Cancel</button></p></dialog>
<script>
let SEGS = [], PROJ = {}, editing = null, approved = false;
const mmss = t => `${~~(t/60)}:${String(~~t%60).padStart(2,'0')}`;
async function j(u, opt) { const r = await fetch(u, opt); if (!r.ok) throw new Error(await r.text()); return r.json(); }

async function load() {
  PROJ = await j('/api/project');
  SEGS = PROJ.segments || [];
  const totalDur = SEGS.length ? SEGS[SEGS.length-1].end : 0;
  const holds = SEGS.filter(s => s.dur > 12).length;
  document.getElementById('hTitle').textContent = PROJ.project || 'active project';
  document.getElementById('hSegs').textContent = SEGS.length;
  document.getElementById('hDur').textContent = mmss(totalDur);
  document.getElementById('hHold').textContent = holds;
  document.getElementById('hAppr').textContent = SEGS.filter(s => s.status === 'approved').length;
  try {
    const u = await j('/api/logs/usage?limit=1');
    const s = u.summary || {};
    document.getElementById('hUsage').textContent =
      `today: ${s.gemini_calls||0} gemini · ${s.tts_chars||0} tts chars · ~$${(s.est_cost_usd||0).toFixed(2)}`;
  } catch (e) {}
  try {
    const a = await j('/api/storyboard/approval');
    setApproved(a.approved);
  } catch (e) {}
  const tbl = document.getElementById('tbl');
  [...tbl.querySelectorAll('tr')].slice(1).forEach(r => r.remove());
  for (const s of SEGS) tbl.appendChild(row(s));
}

function motionLabel(s) {
  const ar = (s.height && s.width) ? s.height / s.width : 0;
  if (s.crop_bbox_norm) return 'planned sub-crop + Ken Burns';
  if (ar >= 3) return `tall strip → scroll-pan top→bottom over ${s.dur.toFixed(1)}s`;
  return (s.seg_index % 2 === 0 ? 'slow push-in' : 'slow pull-out') + ' (Ken Burns)';
}

function row(s) {
  const tr = document.createElement('tr');
  tr.className = s.status === 'approved' ? 'st-approved' : (s.status === 'rejected' ? 'st-rejected' : '');
  const beats = (s.beats || []).map(b =>
    `<div class="beat"><span class="bt">[${(b.start - s.start).toFixed(1)}s]</span> ${esc(b.text)}</div>`).join('');
  tr.innerHTML = `
    <td class="n">${s.seg_index}</td>
    <td class="tc"><b>${mmss(s.start)}</b> → ${mmss(s.end)}<br><span class="dur">${s.dur.toFixed(1)}s</span>
      ${s.dur > 12 ? '<br><span class="longhold">LONG HOLD</span>' : ''}</td>
    <td class="img"><img loading="lazy" src="/panelimg/${encodeURIComponent(s.panel_id)}" onclick="window.open(this.src)"><div class="pid">${s.panel_id}</div></td>
    <td class="mo">IN: cut + 0.4s fade-up<br>HOLD: ${motionLabel(s)}<br>OUT: cut</td>
    <td class="beats">${beats}</td>
    <td class="act">
      <button class="a" onclick="openCands(${s.seg_index})">🔄 Swap panel</button>
      <button class="a" onclick="openEdit(${s.seg_index})">✏️ Edit narration</button>
      <button class="a" onclick="setStatus(${s.seg_index}, 'approved')">✅ Approve</button>
      <button class="a" onclick="setStatus(${s.seg_index}, 'rejected')">🗑 Reject/junk</button>
    </td>`;
  return tr;
}
const esc = t => t.replace(/[&<>]/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;'}[c]));

async function openCands(i) {
  const c = await j(`/api/segments/${i}/candidates`);
  const list = document.getElementById('candList');
  list.innerHTML = '';
  for (const p of (c.candidates || [])) {
    const im = document.createElement('img');
    im.src = `/panelimg/${encodeURIComponent(p.panel_id)}`;
    im.title = p.panel_id;
    im.onclick = async () => {
      await j(`/api/segments/${i}/panel`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({panel_id: p.panel_id})});
      document.getElementById('cands').style.display = 'none';
      load();
    };
    list.appendChild(im);
  }
  document.getElementById('cands').style.display = 'block';
}

function openEdit(i) {
  editing = i;
  const s = SEGS.find(x => x.seg_index === i);
  document.getElementById('editTxt').value = (s.beats || []).map(b => b.text).join(' ');
  document.getElementById('editDlg').showModal();
}
async function saveEdit() {
  const txt = document.getElementById('editTxt').value.trim();
  document.getElementById('editDlg').close();
  await j(`/api/segments/${editing}/narration`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({text: txt})});
  load();
}
async function setStatus(i, st) {
  await j(`/api/segments/${i}/status`, {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({status: st, note: ''})});
  load();
}
function setApproved(v) {
  approved = v;
  const b = document.getElementById('approveBtn');
  b.textContent = v ? '✔ PROJECT APPROVED — renders unlocked' : 'APPROVE PROJECT FOR RENDER';
  b.className = v ? '' : 'off';
}
async function toggleProjectApproval() {
  const r = await j('/api/storyboard/approve', {method:'POST', headers:{'Content-Type':'application/json'}, body: JSON.stringify({approved: !approved})});
  setApproved(r.approved);
}
load();
</script></body></html>"""
