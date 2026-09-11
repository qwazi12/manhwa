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
.back { font-size:12.5px; text-decoration:none; font-weight:600; padding:7px 13px;
  border-radius:6px; background:var(--panel2); border:1px solid var(--rule); color:var(--ink);
  white-space:nowrap; }
.back:hover { border-color:var(--accent); color:var(--accent); }
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
  <a class="back" href="/storyboard">← Back to the board</a>
  <select id="picker" onchange="pick(this.value)"></select>
  <span id="hdrstate"></span>
</header>
<div id="root"><div class="empty">loading…</div></div>
<script>
var DATA = null;
var PUB = null;
var ALL = [];
var YT = null;
var OS = null;
var ELIG = null;
var PUBST = null;
var pubPoll = false;

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

async function load(name, proj) {
  proj = proj || q('project');
  // Every export in the library, so the picker is not limited to whichever
  // project happens to be active — an ingest can change that underneath you.
  try { ALL = (await api('/api/exports?cb=' + Date.now())).exports || []; }
  catch (e) { ALL = []; }
  var u = '/api/review?cb=' + Date.now();
  if (proj) u += '&project=' + encodeURIComponent(proj);
  if (name) u += '&name=' + encodeURIComponent(name);
  try {
    DATA = await api(u);
    try { OS = await api('/api/outstand/status?cb=' + Date.now()); }
    catch (e3) { OS = null; }
    var qs = (DATA.project ? '&project=' + encodeURIComponent(DATA.project) : '') +
             (DATA.name ? '&name=' + encodeURIComponent(DATA.name) : '');
    try { ELIG = await api('/api/outstand/eligibility?cb=' + Date.now() + qs); }
    catch (e5) { ELIG = null; }
    try { PUBST = await api('/api/outstand/publish/status?cb=' + Date.now() + qs); }
    catch (e6) { PUBST = null; }
    try { YT = null; } catch (e4) { YT = null; }
    try {
      PUB = await api('/api/publish?cb=' + Date.now() +
        (DATA.project ? '&project=' + encodeURIComponent(DATA.project) : '') +
        (DATA.name ? '&name=' + encodeURIComponent(DATA.name) : ''));
    } catch (e2) { PUB = null; }
  } catch (e) {
    document.getElementById('root').innerHTML =
      '<div class="empty">Could not load review data — ' + esc(e.message) + '</div>';
    return;
  }
  render();
}

function pick(v) {
  var i = v.indexOf('|');
  load(v.slice(i + 1), v.slice(0, i));
}

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

  var list = ALL.length ? ALL : (d.exports || []).map(function (e) {
    return { name: e.name, project: d.project, size_mb: e.size_mb,
             review_status: e.status, superseded: e.superseded };
  });
  sel.innerHTML = list.map(function (e) {
    var st = e.review_status || e.status;
    var mark = e.superseded ? ' — superseded' :
      (st === 'approved' ? ' — approved' :
       (st === 'sent_back' ? ' — sent back' : ' — not reviewed'));
    var val = (e.project || d.project) + '|' + e.name;
    var on = (e.name === d.name && (e.project || d.project) === d.project);
    return '<option value="' + esc(val) + '"' + (on ? ' selected' : '') + '>' +
      esc(e.project || d.project) + ' · ' + esc(e.name) +
      ' (' + e.size_mb + ' MB)' + esc(mark) + '</option>';
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
        '<div class="hint" style="margin-bottom:8px">' +
          '<b>Approve</b> unlocks Publish preparation below for this export. ' +
          '<b>Send back</b> posts your notes to the top of the board so the ' +
          'next edit starts with them, and keeps publishing locked. ' +
          'Re-render after editing and the new export arrives here unreviewed.' +
        '</div>' +
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
      publishCard() +
      publishNowCard() +
      historyCard(rv) +
    '</div><div>' + outstandCard() + qcCard(d, qc) + '</div></div>';
}

function publishCard() {
  if (!PUB || PUB.missing) return '';
  var md = PUB.metadata || {}, rd = PUB.readiness || {}, probs = PUB.problems || [];
  var locked = !rd.ready;

  var blockers = locked
    ? '<div class="banner b-warn" style="margin:0 0 11px">' +
      (rd.blockers || []).map(esc).join(' ') + '</div>'
    : '';
  var problems = probs.length
    ? '<div class="banner b-bad" style="margin:11px 0 0">' +
      probs.map(esc).join('<br>') + '</div>'
    : '';

  var cats = Object.keys(PUB.categories || {}).map(function (k) {
    return '<option value="' + esc(k) + '"' +
      (String(md.category_id) === k ? ' selected' : '') + '>' +
      esc(PUB.categories[k]) + '</option>';
  }).join('');
  var privs = (PUB.privacy_options || []).map(function (v) {
    return '<option value="' + esc(v) + '"' +
      (md.privacy === v ? ' selected' : '') + '>' + esc(v) + '</option>';
  }).join('');

  var dis = locked ? ' disabled' : '';
  var th = md.thumbnail || {};
  return '<div class="card"><h2>Publish preparation</h2>' + blockers +
    '<div class="hint" style="margin-bottom:10px">' +
      'These details are sent with the video. <b>Publish to</b> chooses which ' +
      'connected accounts receive it, and <b>Privacy</b> decides who can see it — ' +
      'private is the default and the only option enabled in this pass. ' +
      'Changes save as you make them; <b>Download upload package</b> gives you the ' +
      'same details as a file if you would rather upload by hand.' +
    '</div>' +
    fld('Title', '<input id="p_title" maxlength="' + (PUB.limits || {}).title +
        '" value="' + esc(md.title) + '" onchange="savePublish()"' + dis + '>') +
    fld('Description — becomes the video description',
        '<textarea id="p_desc" style="min-height:90px" onchange="savePublish()"' + dis + '>' +
        esc(md.description) + '</textarea>') +
    fld('Tags — comma separated', '<input id="p_tags" value="' +
        esc((md.tags || []).join(', ')) + '" onchange="savePublish()"' + dis + '>') +
    fld('Category', '<select id="p_cat" onchange="savePublish()"' + dis + '>' + cats + '</select>') +
    fld('Privacy', '<select id="p_priv" onchange="savePublish()"' + dis + '>' + privs + '</select>') +
    fld('Publish to — hold ⌘ or Ctrl to pick several', targetPicker(md, dis)) +
    fld('Schedule — optional, private only',
        '<input id="p_at" placeholder="2026-09-20T15:00:00Z" value="' +
        esc(md.publish_at || '') + '" onchange="savePublish()"' + dis + '>') +
    fld('Playlist — not sent yet, kept for the manual package',
        '<input id="p_play" value="' + esc(md.playlist || '') + '" onchange="savePublish()"' + dis + '>') +
    fld('Thumbnail from segment # — not sent yet, kept for the manual package',
        '<input id="p_thumb" type="number" min="0" value="' +
        (th.seg_index != null ? th.seg_index : '') +
        '" oninput="thumbPreview()" onchange="savePublish()"' + dis + '>' +
        '<div id="p_thumbprev" style="margin-top:6px"></div>') +
    '<label style="display:block;margin:8px 0"><input type="checkbox" id="p_kids"' +
      (md.made_for_kids ? ' checked' : '') + ' onchange="savePublish()"' + dis +
      '> Made for kids</label>' +
    '<label style="display:block;margin:8px 0"><input type="checkbox" id="p_synth"' +
      (md.synthetic_disclosure ? ' checked' : '') + ' onchange="savePublish()"' + dis +
      '> Contains AI-generated narration — disclosed on upload</label>' +
    '<div class="actions">' +
      '<button onclick="savePublish()"' + dis + '>Save</button>' +
      '<button onclick="downloadPackage()"' + dis + '>⬇ Download upload package</button>' +
      '<span id="p_saved" class="hint"></span>' +
    '</div>' + problems + '</div>';
}

function targetPicker(md, dis) {
  // A multi-select, not checkboxes: the list grows as accounts are added and a
  // dropdown stays the same size whether there is one account or twenty.
  var accounts = ((OS || {}).accounts || []).filter(function (a) { return a.active; });
  if (!accounts.length) {
    return '<div class="hint">No connected accounts yet — link one in ' +
      '<b>Publishing accounts</b> above.</div>';
  }
  var chosen = md.targets || [];
  var opts = accounts.map(function (a) {
    var on = chosen.indexOf(a.account_id) !== -1;
    return '<option value="' + esc(a.account_id) + '"' + (on ? ' selected' : '') + '>' +
      esc(a.network || '?') + ' · ' + esc(a.username || a.account_id) + '</option>';
  }).join('');
  return '<select id="p_targets" class="os_target" multiple size="' +
    Math.min(Math.max(accounts.length, 2), 6) + '" onchange="savePublish()"' + dis +
    ' style="width:100%">' + opts + '</select>';
}

function fld(label, control) {
  return '<div style="margin:9px 0"><div class="hint" style="margin-bottom:3px">' +
    esc(label) + '</div>' + control + '</div>';
}

function thumbPreview() {
  var v = (document.getElementById('p_thumb') || {}).value;
  var box = document.getElementById('p_thumbprev');
  if (!box) return;
  box.innerHTML = (v === '' || v == null) ? '' :
    '<img src="/segimg/' + encodeURIComponent(v) + '?cb=' + Date.now() +
    '" style="max-width:150px;border-radius:5px;border:1px solid #272b38">';
}

function collectPublish() {
  var t = (document.getElementById('p_thumb') || {}).value;
  return {
    title: (document.getElementById('p_title') || {}).value || '',
    description: (document.getElementById('p_desc') || {}).value || '',
    tags: ((document.getElementById('p_tags') || {}).value || '')
      .split(',').map(function (x) { return x.trim(); }).filter(Boolean),
    category_id: (document.getElementById('p_cat') || {}).value || '1',
    privacy: (document.getElementById('p_priv') || {}).value || 'private',
    publish_at: (document.getElementById('p_at') || {}).value || '',
    playlist: (document.getElementById('p_play') || {}).value || '',
    made_for_kids: !!(document.getElementById('p_kids') || {}).checked,
    synthetic_disclosure: !!(document.getElementById('p_synth') || {}).checked,
    targets: Array.from(
      ((document.getElementById('p_targets') || {}).selectedOptions) || []
    ).map(function (o) { return o.value; }),
    thumbnail: (t === '' || t == null) ? null
      : { type: 'segment', seg_index: parseInt(t, 10) }
  };
}

async function savePublish() {
  var el = document.getElementById('p_saved');
  if (el) el.textContent = 'saving…';
  try {
    PUB = await api('/api/publish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: DATA.project, name: DATA.name,
                             metadata: collectPublish() })
    });
    // Eligibility depends on what was just saved (targets, privacy), so
    // refresh it here — otherwise picking an account leaves Publish disabled.
    var qs = '&project=' + encodeURIComponent(DATA.project) +
             '&name=' + encodeURIComponent(DATA.name);
    try { ELIG = await api('/api/outstand/eligibility?cb=' + Date.now() + qs); }
    catch (e9) { /* keep the previous eligibility on screen */ }
    render();
    var e2 = document.getElementById('p_saved');
    if (e2) e2.textContent = (PUB.problems && PUB.problems.length) ? 'saved, with problems' : 'saved';
  } catch (e) {
    if (el) el.textContent = '';
    alert('Could not save: ' + e.message);
  }
}

function downloadPackage() {
  window.location = '/api/publish/package?project=' + encodeURIComponent(DATA.project) +
    '&name=' + encodeURIComponent(DATA.name);
}

function publishNowCard() {
  if (!ELIG) return '';
  var pub = (PUBST || {}).publish || null;
  var results = (pub || {}).results || [];
  var st = (pub || {}).status;

  var resultRows = results.map(function (x) {
    var cls = x.status === 'published' ? 'p-ok'
            : (x.status === 'failed' ? 'p-bad' : 'p-neutral');
    var link = x.url
      ? ' <a href="' + esc(x.url) + '" target="_blank">open</a>'
      : '';
    return '<div class="row"><span>' + esc(x.network || '?') + ' · ' +
      esc(x.username || x.account_id || '') + '</span><span>' +
      pill(x.status || 'pending', cls) + link + '</span></div>' +
      (x.error ? '<div class="hint" style="color:#ef6470">' + esc(x.error) + '</div>' : '');
  }).join('');

  var body = '', actions = '';

  if (st === 'in_progress') {
    body = '<div class="hint">' + esc((pub || {}).stage || 'working…') + '</div>' + resultRows;
    actions = '<button disabled>Publishing…</button>';
    if (!pubPoll) { pubPoll = true; setTimeout(pollPublish, 5000); }
  } else if (results.length) {
    body = resultRows +
      '<div class="hint" style="margin-top:7px">Overall: <b>' + esc(st || '?') + '</b></div>';
    actions = '<button disabled title="already published from this export">Published</button>';
  } else if (!ELIG.ready) {
    body = '<div class="banner b-warn" style="margin:0">' +
      (ELIG.blockers || []).map(esc).join('<br>') + '</div>';
    actions = '<button disabled>Publish</button>';
  } else {
    body = '<div class="hint">Ready. This will upload the video to Outstand and ' +
      'post it to the selected account(s) as <b>' + esc(ELIG.effective_privacy || 'private') +
      '</b>.</div>';
    actions = '<button class="ok" onclick="doPublish()">▶ Publish now</button>';
  }

  var badge = st === 'published' ? pill('published', 'p-ok')
    : (st === 'partial' ? pill('partial', 'p-warn')
    : (st === 'failed' ? pill('failed', 'p-bad')
    : (st === 'in_progress' ? pill('publishing', 'p-blue') : pill('not published', 'p-neutral'))));

  return '<div class="card"><h2>Publish ' + badge + '</h2>' + body +
    '<div class="actions">' + actions + '</div></div>';
}

async function pollPublish() {
  try {
    var qs = '&project=' + encodeURIComponent(DATA.project) +
             '&name=' + encodeURIComponent(DATA.name);
    PUBST = await api('/api/outstand/publish/status?cb=' + Date.now() + qs);
    render();
    var st = ((PUBST || {}).publish || {}).status;
    if (st === 'in_progress') { setTimeout(pollPublish, 5000); return; }
  } catch (e) { /* leave the last state on screen */ }
  pubPoll = false;
}

async function doPublish() {
  var priv = (ELIG || {}).effective_privacy || 'private';
  var who = ((OS || {}).accounts || []).filter(function (a) { return a.active; })
    .map(function (a) { return a.username || a.account_id; }).join(', ');
  if (!confirm('Publish this video to ' + who + ' as ' + priv +
      '? It will be uploaded to Outstand and posted. Confirm you have the right ' +
      'to publish this artwork.')) return;
  try {
    await api('/api/outstand/publish', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ project: DATA.project, name: DATA.name })
    });
    pubPoll = true;
    setTimeout(pollPublish, 2000);
    await load(DATA.name, DATA.project);
  } catch (e) { alert('Could not start publishing: ' + e.message); }
}

function historyCard(rv) {
  if (!rv.history || !rv.history.length) return '';
  return '<div class="card"><h2>Earlier decisions</h2>' + rv.history.slice().reverse().map(function (h) {
    return '<div class="row"><span>' + esc(h.status) + '</span><span>' +
      (h.at ? new Date(h.at * 1000).toLocaleDateString() : '') + '</span></div>' +
      (h.notes ? '<div class="hint">' + esc(h.notes) + '</div>' : '');
  }).join('') + '</div>';
}

function outstandCard() {
  if (!OS) return '';
  var accounts = OS.accounts || [];
  var body = '', actions = '';

  if (!OS.configured) {
    body = '<div class="hint">' + esc(OS.detail) + '</div>' +
      '<div class="hint" style="margin-top:7px">Missing: <b>' +
      esc((OS.missing || []).join(', ')) + '</b></div>';
    actions = '<button disabled title="configure Outstand first">Connect an account</button>';
  } else {
    body = accounts.length
      ? accounts.map(function (a) {
          return '<div class="row"><span>' +
            (a.active ? pill(a.network || '?', 'p-blue') : pill('inactive', 'p-neutral')) +
            ' ' + esc(a.username || a.nickname || a.account_id) + '</span>' +
            '<span><button class="mini" onclick="disconnectAcct(' +
            JSON.stringify(a.account_id) + ')">remove</button></span></div>';
        }).join('')
      : '<div class="hint">' + esc(OS.detail) + '</div>';
    var nets = (OS.networks || ['youtube']).map(function (n) {
      return '<option value="' + esc(n) + '"' + (n === 'youtube' ? ' selected' : '') +
        '>' + esc(n) + '</option>';
    }).join('');
    actions = '<select id="os_net" style="max-width:150px">' + nets + '</select>' +
      '<button onclick="connectOutstand()">Connect</button>' +
      '<button onclick="refreshOutstand()">Refresh</button>';
  }

  var badge = !OS.configured ? pill('not set up', 'p-neutral')
    : (OS.n_active ? pill(OS.n_active + ' connected', 'p-ok') : pill('no accounts', 'p-warn'));

  return '<div class="card"><h2>Publishing accounts ' + badge + '</h2>' + body +
    '<div class="actions">' + actions + '</div>' +
    '<div class="hint" style="margin-top:8px">Accounts are linked through Outstand, ' +
    'so this tool never stores a YouTube password or token. Direct publishing is ' +
    'available once an account is connected and an export is approved.</div>' +
    '</div>';
}

function connectOutstand() {
  var n = (document.getElementById('os_net') || {}).value || 'youtube';
  location.href = '/api/outstand/connect?network=' + encodeURIComponent(n);
}

async function refreshOutstand() {
  try {
    OS = await api('/api/outstand/refresh', { method: 'POST' });
    render();
  } catch (e) { alert('Could not refresh: ' + e.message); }
}

async function disconnectAcct(id) {
  if (!confirm('Remove this account from the tool? It stays linked inside Outstand.')) return;
  try {
    await api('/api/outstand/disconnect', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ account_id: id })
    });
    await load(DATA.name, DATA.project);
  } catch (e) { alert('Could not remove: ' + e.message); }
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
    await load(DATA.name, DATA.project);
    var e2 = document.getElementById('saved');
    if (e2) e2.textContent = 'saved';
  } catch (e) {
    if (el) el.textContent = '';
    alert('Could not save: ' + e.message);
  }
}
function decide(status) { send(status); }
function saveNotes() { send(''); }

load(q('name'), q('project'));
</script></body></html>
"""


def build_review_html(pdir=None):
    """The review page. Static shell — it fetches /api/review itself."""
    return _PAGE
