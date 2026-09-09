"""Phase A — the /review page: watch a rendered export and rule on it.

Deliberately a FULL PAGE, not a drawer: the drawers are narrow side panels and
a video needs width. Precedent for a nav entry that is a link rather than a
toggle already exists (the Legacy button).

The HTML below is a PLAIN string, not an f-string. storyboard.py is one large
f-string, which means every brace in its JavaScript must be doubled and every
backslash escaped — a literal newline written as \\n inside a JS string has
broken the board twice (Session 24 P0, and again in Session 27). Interpolating
nothing here removes that whole class of bug; the page fetches its own data
from /api/review on load.
"""
import json
import os

_PAGE = """<!doctype html>
<html lang="en"><head><meta charset="utf-8">
<title>Review — Manhwa Recap Studio</title>
<style>
:root {
  --bg:#0e1016; --panel:#161923; --panel2:#1b1f2a; --rule:#272b38;
  --ink:#e9ebf2; --ink2:#b2b8c8; --ink3:#858ca0;
  --accent:#5b8cff; --ok:#39c07f; --warn:#e0a33a; --bad:#ef5f6b;
}
* { box-sizing:border-box; }
body { margin:0; background:var(--bg); color:var(--ink);
  font:14px/1.55 -apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif; }
a { color:var(--accent); }
header { display:flex; gap:16px; align-items:center; padding:12px 20px;
  border-bottom:1px solid var(--rule); background:var(--panel); flex-wrap:wrap; }
header h1 { font-size:15px; margin:0; font-weight:700; letter-spacing:-.01em; }
.back { font-size:12px; text-decoration:none; }
.wrap { display:grid; grid-template-columns:minmax(0,1fr) 340px; gap:20px;
  padding:20px; align-items:start; }
@media (max-width:980px) { .wrap { grid-template-columns:1fr; } }
video { width:100%; background:#000; border-radius:8px; display:block; }
.card { background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  padding:14px; margin-bottom:14px; }
.card h2 { font-size:12px; text-transform:uppercase; letter-spacing:.09em;
  color:var(--ink3); margin:0 0 10px; font-weight:700; }
.row { display:flex; justify-content:space-between; gap:10px; padding:4px 0;
  border-bottom:1px solid var(--panel2); font-size:12.5px; }
.row:last-child { border-bottom:none; }
.row span:first-child { color:var(--ink3); }
.pill { display:inline-block; font-size:10.5px; font-weight:700; padding:2px 8px;
  border-radius:99px; letter-spacing:.03em; }
.p-ok { background:#13291f; color:var(--ok); }
.p-warn { background:#2d2413; color:var(--warn); }
.p-bad { background:#2e161a; color:var(--bad); }
.p-neutral { background:#1e222d; color:var(--ink3); }
.p-blue { background:#1a2340; color:var(--accent); }
button { font:inherit; border-radius:6px; cursor:pointer; border:1px solid var(--rule);
  background:var(--panel2); color:var(--ink); padding:7px 14px; }
button:hover:not(:disabled) { border-color:var(--accent); }
button:disabled { opacity:.5; cursor:not-allowed; }
button.ok { background:#16351f; border-color:#2c6b45; color:#9be8bd; font-weight:700; }
button.back-btn { background:#3a2412; border-color:#7a4a1c; color:#ffcf9b; font-weight:700; }
textarea { width:100%; min-height:80px; background:var(--panel2); color:var(--ink);
  border:1px solid var(--rule); border-radius:6px; padding:9px; font:inherit; resize:vertical; }
select { background:var(--panel2); color:var(--ink); border:1px solid var(--rule);
  border-radius:6px; padding:6px 9px; font:inherit; max-width:360px; }
.banner { padding:11px 14px; border-radius:8px; margin-bottom:14px; font-size:13px; }
.b-warn { background:#2d2413; border-left:3px solid var(--warn); color:#f0d6a2; }
.b-bad { background:#2e161a; border-left:3px solid var(--bad); color:#ffc4c9; }
.b-info { background:#141a2c; border-left:3px solid var(--accent); color:#c8d6ff; }
.hint { color:var(--ink3); font-size:11.5px; }
.qc li { margin:3px 0; font-size:12.5px; color:var(--ink2); }
.qc ul { margin:6px 0 0; padding-left:18px; }
.actions { display:flex; gap:9px; flex-wrap:wrap; margin-top:11px; }
.empty { padding:40px 20px; text-align:center; color:var(--ink3); }
</style></head><body>
<header>
  <h1>📺 Review</h1>
  <a class="back" href="/storyboard">← back to the board</a>
  <select id="picker" onchange="pick(this.value)"></select>
  <span id="hdrstate"></span>
</header>
<div id="root"><div class="empty">loading…</div></div>
<script>
var DATA = null;

function esc(s) {
  return String(s == null ? '' : s).replace(/[&<>"]/g, function (c) {
    return {'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c];
  });
}
function q(name) {
  var m = new RegExp('[?&]' + name + '=([^&]*)').exec(location.search);
  return m ? decodeURIComponent(m[1]) : '';
}
function pill(text, cls) { return '<span class="pill ' + cls + '">' + esc(text) + '</span>'; }

function statusPill(st, superseded) {
  if (superseded) return pill('superseded', 'p-warn');
  if (st === 'approved') return pill('approved', 'p-ok');
  if (st === 'sent_back') return pill('sent back', 'p-bad');
  return pill('review pending', 'p-neutral');
}

async function api(url, opts) {
  var r = await fetch(url, opts);
  if (!r.ok) {
    var m = await r.text();
    try { m = JSON.parse(m).detail || m; } catch (e) {}
    throw new Error(m);
  }
  return r.json();
}

async function load(name) {
  var proj = q('project');
  var u = '/api/review?cb=' + Date.now();
  if (proj) u += '&project=' + encodeURIComponent(proj);
  if (name) u += '&name=' + encodeURIComponent(name);
  try {
    DATA = await api(u);
  } catch (e) {
    document.getElementById('root').innerHTML =
      '<div class="empty">Could not load review data — ' + esc(e.message) + '</div>';
    return;
  }
  render();
}

function pick(name) { load(name); }

function render() {
  var d = DATA, root = document.getElementById('root');
  var sel = document.getElementById('picker');

  if (d.missing || !d.name) {
    sel.innerHTML = '';
    document.getElementById('hdrstate').innerHTML = '';
    root.innerHTML = '<div class="empty"><p>Nothing to review yet — ' +
      esc(d.reason || 'no export found') + '.</p>' +
      '<p><a href="/storyboard">Go to the board</a>, tick the segments you want, ' +
      'then approve the project for render.</p></div>';
    return;
  }

  sel.innerHTML = (d.exports || []).map(function (e) {
    var mark = e.superseded ? ' — superseded' :
      (e.status === 'approved' ? ' — approved' :
       (e.status === 'sent_back' ? ' — sent back' : ''));
    return '<option value="' + esc(e.name) + '"' + (e.name === d.name ? ' selected' : '') +
      '>' + esc(e.name) + ' (' + e.size_mb + ' MB)' + esc(mark) + '</option>';
  }).join('');

  var rv = d.review || {}, qc = d.qc || {};
  document.getElementById('hdrstate').innerHTML =
    statusPill(rv.status, rv.superseded) + ' <span class="hint">' + esc(d.project) + '</span>';

  var banners = '';
  if (rv.superseded) {
    banners += '<div class="banner b-warn"><b>This verdict belongs to an older cut.</b> ' +
      'The timeline has changed since it was reviewed (' + esc(rv.cut_signature) +
      ' → ' + esc(rv.current_signature) + '), so re-render and review the new export ' +
      'before trusting this decision.</div>';
  }
  if (!d.file_present) {
    banners += '<div class="banner b-bad">The video file is gone — exports are deleted ' +
      'after 7 days. The review record below is kept.</div>';
  }
  if (rv.status === 'sent_back' && rv.notes) {
    banners += '<div class="banner b-info"><b>Sent back:</b> ' + esc(rv.notes) +
      ' &nbsp;<a href="/storyboard">open the board</a></div>';
  }

  var player = d.file_present
    ? '<video controls preload="metadata" src="' + esc(d.url) + '"></video>'
    : '<div class="card"><div class="hint">No file to play.</div></div>';

  root.innerHTML =
    '<div class="wrap"><div>' + banners + player +
      '<div class="card" style="margin-top:14px"><h2>Verdict</h2>' +
        '<textarea id="notes" placeholder="Notes — what to fix, or why this is good. Saved on its own.">' +
        esc(rv.notes || '') + '</textarea>' +
        '<div class="actions">' +
          '<button class="ok" onclick="decide(\\'approved\\')">✓ Approve</button>' +
          '<button class="back-btn" onclick="decide(\\'sent_back\\')">↩ Send back</button>' +
          '<button onclick="saveNotes()">Save notes only</button>' +
          '<span id="saved" class="hint"></span>' +
        '</div>' +
        '<div class="hint" style="margin-top:9px">' +
          (rv.reviewed_at ? 'Last decision ' + new Date(rv.reviewed_at * 1000).toLocaleString() : 'Not reviewed yet') +
          ' · cut ' + esc(rv.current_signature || '?') +
        '</div>' +
      '</div>' +
      historyCard(rv) +
    '</div><div>' + qcCard(d, qc) + '</div></div>';
}

function historyCard(rv) {
  if (!rv.history || !rv.history.length) return '';
  return '<div class="card"><h2>Earlier decisions</h2>' + rv.history.slice().reverse().map(function (h) {
    return '<div class="row"><span>' + esc(h.status) + '</span><span>' +
      (h.at ? new Date(h.at * 1000).toLocaleDateString() : '') + '</span></div>' +
      (h.notes ? '<div class="hint">' + esc(h.notes) + '</div>' : '');
  }).join('') + '</div>';
}

function qcCard(d, qc) {
  var v = qc.validation || {}, out = '';

  out += '<div class="card"><h2>This export</h2>' +
    row('file', esc(d.name)) +
    row('size', (d.stat ? d.stat.size_mb + ' MB' : '—')) +
    row('rendered', (qc.render_job && qc.render_job.ended
        ? new Date(qc.render_job.ended * 1000).toLocaleString() : '—')) +
    row('clips', (qc.render_job ? qc.render_job.clips : '—')) +
    '</div>';

  out += '<div class="card"><h2>The cut</h2>' +
    row('in video', qc.segments_in_video + ' of ' + qc.segments_total + ' segments') +
    row('runtime', qc.runtime_s + 's') +
    row('pages', qc.n_pages == null ? '—' : qc.n_pages) +
    '</div>';

  var mm = qc.semantic ? pill('semantic', 'p-ok') : pill('lexical', 'p-bad');
  out += '<div class="card"><h2>Matching</h2>' +
    '<div class="row"><span>method</span><span>' + mm + '</span></div>' +
    (qc.embed_fallback_reason
      ? '<div class="hint" style="margin-top:6px">' + esc(qc.embed_fallback_reason) + '</div>'
      : '') +
    (!qc.semantic
      ? '<div class="hint" style="margin-top:6px">Panels were matched on word overlap, ' +
        'so they often sit on the wrong sentence. Re-ingest before trusting the pacing.</div>'
      : '') +
    '</div>';

  var checks = '';
  checks += checkRow('timing validation',
    v.ok === true ? 'clean' : (v.ok === false ? (v.errors || []).length + ' error(s)' : 'unknown'),
    v.ok === true ? 'p-ok' : (v.ok === false ? 'p-bad' : 'p-neutral'));
  checks += checkRow('long holds (>12s)', (qc.long_holds || []).length,
    (qc.long_holds || []).length ? 'p-warn' : 'p-ok');
  checks += checkRow('silent segments', (qc.silent_segments || []).length,
    (qc.silent_segments || []).length ? 'p-warn' : 'p-ok');
  checks += checkRow('crop warnings',
    (v.warnings || []).filter(function (w) { return String(w.rule || '').indexOf('C2') === 0; }).length,
    'p-neutral');
  out += '<div class="card qc"><h2>Quality checks</h2>' + checks;

  if ((qc.long_holds || []).length) {
    out += '<ul>' + qc.long_holds.slice(0, 5).map(function (h) {
      return '<li>' + esc(h.panel) + ' — ' + h.seconds + 's</li>';
    }).join('') + '</ul>';
  }
  if ((v.errors || []).length) {
    out += '<ul>' + v.errors.slice(0, 5).map(function (e) {
      return '<li style="color:#ef5f6b">seg ' + e.seg + ' — ' + esc(e.msg) + '</li>';
    }).join('') + '</ul>';
  }
  if (qc.scrape_warning) {
    out += '<div class="hint" style="margin-top:8px">⚠ ' + esc(qc.scrape_warning) + '</div>';
  }
  out += '</div>';
  return out;
}

function row(k, v) { return '<div class="row"><span>' + esc(k) + '</span><span>' + v + '</span></div>'; }
function checkRow(k, v, cls) {
  return '<div class="row"><span>' + esc(k) + '</span><span>' + pill(v, cls) + '</span></div>';
}

async function send(status) {
  var notes = (document.getElementById('notes') || {}).value || '';
  var body = { project: DATA.project, name: DATA.name, notes: notes };
  if (status) body.status = status;
  var el = document.getElementById('saved');
  if (el) el.textContent = 'saving…';
  try {
    await api('/api/review', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body)
    });
    await load(DATA.name);
    var e2 = document.getElementById('saved');
    if (e2) e2.textContent = 'saved';
  } catch (e) {
    if (el) el.textContent = '';
    alert('Could not save: ' + e.message);
  }
}
function decide(status) { send(status); }
function saveNotes() { send(''); }

load(q('name'));
</script></body></html>
"""


def build_review_html(pdir=None):
    """The review page. Static shell — it fetches /api/review itself."""
    return _PAGE
