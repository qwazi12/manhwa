"""One palette, one rail, one theme switch — shared by every page.

The board and /review used to carry hand-copied colour values. They drifted,
and the owner's complaint ("it's like 2 different worlds") was the result. The
fix is not "copy them again more carefully": it is a single source both pages
import, so there is nowhere for a second copy to live.

Dark is the default because that is what the board became. `data-theme="light"`
on <html> flips every token; nothing else in either page needs to change,
because nothing else names a colour directly.

Contrast note: the first dark pass used --panel2 for button faces, which sat a
few percent away from the panel behind them — the owner described buttons
"blinding into each other". Buttons now have their OWN surface tokens (--btn,
--btn-hover, --btn-edge) that are deliberately lighter than any panel in dark
and deliberately bordered in light, so a control always reads as a control.
"""

# --------------------------------------------------------------- palette
# Every colour in the app resolves to one of these. Adding a colour literal to
# a page is how the two-worlds problem comes back — put it here instead.
TOKENS_CSS = """
:root {
  color-scheme: dark;
  /* Dark palette taken from the Scrapper studio (2026-10-02, owner: "design
     like that"): deep neutral greys, GREEN for primary actions, BLUE for
     links and selection. The earlier neon-cyan set is retired. */
  --bg:#0f1115; --panel:#171a21; --panel2:#1e222b; --rule:#2a2f3a;
  --ink:#e6e9ef; --ink2:#b9c0cd; --ink3:#8b93a3;
  --accent:#4a9eff; --ok:#3ecf7c; --warn:#e5b567; --bad:#ff5c5c;
  --ai:#c07cff;
  --cta:#3ecf7c;      --cta-ink:#06210f;   --cta-hover:#46e089;
  --cta2:#232833;     --cta2-ink:#e6e9ef;  --cta2-hover:#2c333f;
  --ai-cta:#a56bff;   --ai-ink:#16052b;    --ai-hover:#b788ff;
  --ok-cta:#1f7a48;   --ok-cta-ink:#e8fff1;
  --bad-cta:#ff5c5c;  --bad-cta-ink:#2b0505;
  --sel:#4a9eff;
  --applied:#3ecf7c;
  --glow:none;
  --glow-ai:0 0 0 1px rgba(165,107,255,.30);
  --btn:#232833; --btn-hover:#2c333f; --btn-edge:#333a47; --btn-ink:#e6e9ef;
  --focus:#4a9eff; --shadow:rgba(0,0,0,.35);
  --accent-ink:#04121f;
  --sa-bg:#132235;    --sa-ink:#8ec2ff;
  --fold-bg:#2b2414;  --fold-ink:#e5c98a;
  --omit-bg:#311a1d;  --omit-ink:#ff9e9e;
  --gray-bg:#1b1f27;  --gray-ink:#9aa2b2;
  --seg-bg:#14171d;   --seg-rule:#2a2f3a;
  --okb-bg:#10291c;   --okb-ink:#6fe3a4;
  --warnb-bg:#2e2615; --warnb-ink:#ecc98a;
  --badb-bg:#33191b;  --badb-ink:#ffa3a3;
  --tall-bg:#241c38;  --tall-ink:#cdb4ff;
  --tight-bg:#33220f; --tight-ink:#f0b98a;
  /* sidebar + top bar surfaces */
  --side-bg:#0c0e14; --side-foot:#080a0e; --top-bg:#0b0d11;
  --nav-ink:#94a3b8; --nav-on-bg:#1e293b; --nav-on-edge:#334155; --nav-on-ink:#ffffff;
  --badge-ok-bg:#064e3b; --badge-warn-bg:#78350f; --badge-warn-ink:#fbbf24;
  --badge-muted-bg:#1e293b;
  --job-bg:#1e1b4b; --job-ink:#c7d2fe; --job-err-bg:#450a0a; --job-err-ink:#fca5a5;
}
:root[data-theme="light"] {
  color-scheme: light;
  --side-bg:#ffffff; --side-foot:#f2f5fa; --top-bg:#ffffff;
  --nav-ink:#475569; --nav-on-bg:#e8eef8; --nav-on-edge:#c2cbdb; --nav-on-ink:#0f1420;
  --badge-ok-bg:#d6f5e5; --badge-warn-bg:#fbeed0; --badge-warn-ink:#6d4a00;
  --badge-muted-bg:#e7ebf3;
  --job-bg:#eef0ff; --job-ink:#3730a3; --job-err-bg:#fde2e2; --job-err-ink:#991b1b;
  --bg:#f2f5fa; --panel:#ffffff; --panel2:#e9edf5; --rule:#c2cbdb;
  --ink:#0f1420; --ink2:#414b5e; --ink3:#5b6478;
  /* Light mode keeps the vividness by SATURATING rather than brightening:
     a neon fill on white needs dark-enough pigment to carry white ink. */
  --accent:#0a66d6; --ok:#0a7f4e; --warn:#8a5500; --bad:#c8203f;
  --ai:#7b2ff7;
  --cta:#0a66d6;      --cta-ink:#ffffff;   --cta-hover:#2f80e8;
  --cta2:#e3e9f5;     --cta2-ink:#0f1420;  --cta2-hover:#d3dcee;
  --ai-cta:#7b2ff7;   --ai-ink:#ffffff;    --ai-hover:#9354ff;
  --ok-cta:#0a9d5e;   --ok-cta-ink:#ffffff;
  --bad-cta:#e11d48;  --bad-cta-ink:#ffffff;
  --sel:#0a66d6;
  --applied:#0a9d5e;
  --glow:0 0 0 1px rgba(10,102,214,.30), 0 4px 16px -4px rgba(10,102,214,.45);
  --glow-ai:0 0 0 1px rgba(123,47,247,.30), 0 4px 16px -4px rgba(123,47,247,.45);
  --btn:#ffffff; --btn-hover:#eef2fa; --btn-edge:#9aa6bd; --btn-ink:#0f1420;
  --focus:#0a66d6; --shadow:rgba(16,24,40,.18);
  --accent-ink:#ffffff;
  --sa-bg:#dfebff;    --sa-ink:#0d3f86;
  --fold-bg:#fcf0d2;  --fold-ink:#6a4c00;
  --omit-bg:#fce2e6;  --omit-ink:#8f1d31;
  --gray-bg:#e7ebf3;  --gray-ink:#525b6e;
  --seg-bg:#f7f9fd;   --seg-rule:#d8e0ee;
  --okb-bg:#d6f5e5;   --okb-ink:#065f3c;
  --warnb-bg:#fbeed0; --warnb-ink:#6d4a00;
  --badb-bg:#fce0e6;  --badb-ink:#98182f;
  --tall-bg:#e9e0ff;  --tall-ink:#4527a0;
  --tight-bg:#ffe6d5; --tight-ink:#8a3a0d;
}
"""

# ---------------------------------------------------------------- controls
# One rule set for every clickable thing on both pages. `button` is styled
# element-wide so a control added later is correct without being remembered.
CONTROLS_CSS = """
/* One control language for the whole app. The rule is hierarchy: a filled,
   glowing control is a primary action; a bordered one is secondary; plain text
   is information. Before this, every button shared one dull slate surface, so
   "Generate SEO suggestions" looked exactly like "Choose file". */
button, .cropact {
  font:inherit; background:var(--btn); color:var(--btn-ink);
  border:1px solid var(--btn-edge); border-radius:7px; cursor:pointer;
  font-weight:600; box-shadow:0 1px 0 var(--shadow);
  transition:background .12s, border-color .12s, box-shadow .12s, transform .06s;
}
button:hover:not(:disabled), .cropact:hover {
  background:var(--btn-hover); border-color:var(--accent);
}
button:active:not(:disabled) { transform:translateY(1px); }
button:disabled { opacity:.42; cursor:not-allowed; box-shadow:none; }

/* Primary: filled + glow. Impossible to mistake for a label. */
button.primary, button.cta, .drawer button.primary {
  background:var(--cta); border-color:var(--cta); color:var(--cta-ink);
  font-weight:800; box-shadow:var(--glow); letter-spacing:.01em;
}
button.primary:hover:not(:disabled), button.cta:hover:not(:disabled),
.drawer button.primary:hover:not(:disabled) {
  background:var(--cta-hover); border-color:var(--cta-hover);
}
/* The AI/SEO family gets its OWN accent so generated material is never
   confused with the operator's own final values. */
button.ai {
  background:var(--ai-cta); border-color:var(--ai-cta); color:var(--ai-ink);
  font-weight:800; box-shadow:var(--glow-ai);
}
button.ai:hover:not(:disabled) { background:var(--ai-hover); border-color:var(--ai-hover); }
button.ai-ghost {
  background:transparent; color:var(--ai); border-color:var(--ai); font-weight:700;
}
button.ai-ghost:hover:not(:disabled) { background:var(--ai-cta); color:var(--ai-ink); }

button.ok, button.go {
  background:var(--ok-cta); border-color:var(--ok-cta); color:var(--ok-cta-ink);
  font-weight:800;
}
button.danger {
  background:transparent; color:var(--bad); border-color:var(--bad); font-weight:700;
}
button.danger:hover:not(:disabled) { background:var(--bad-cta); color:var(--bad-cta-ink); }
button.back-btn {
  background:var(--warnb-bg); border-color:var(--warn); color:var(--warnb-ink);
  font-weight:700;
}
/* A control whose value is already applied stops shouting and reports. */
button.applied, button[data-applied="1"] {
  background:var(--okb-bg); border-color:var(--applied); color:var(--applied);
  box-shadow:none; font-weight:700;
}
button.big { padding:11px 18px; font-size:14px; }

/* Selected / active states that actually read as selected. */
.is-active, .selected { outline:2px solid var(--sel); outline-offset:1px; }

button:focus-visible, .cropact:focus-visible, a:focus-visible,
input:focus-visible, textarea:focus-visible, select:focus-visible,
summary:focus-visible, .navbtn:focus-visible {
  outline:2px solid var(--focus); outline-offset:2px;
}
input[type=checkbox] { accent-color:var(--sel); }
input:focus, textarea:focus, select:focus { border-color:var(--accent); }
"""

# ------------------------------------------------------------------- rail
def rail_css(sel, guard=None):
    """Shared rail styling.

    THE RAIL NO LONGER RETRACTS. It used to auto-hide to a 14px sliver and
    slide back out on hover or focus; the owner asked for that removed
    (2026-09-13) — "theres no need for the side bar to always pop out when i
    in board doing something unrelated to the sidebar". So the rail is simply
    always at its full width, which is what it was before auto-hide existed.

    Removing it also removes a whole class of bug for free: a rail that is
    never in two states cannot paint in the wrong one, cannot flash open while
    a 400KB board parses, and cannot reopen under the pointer.

    `sel` and `guard` are kept so both callers (/storyboard's '#rail' and
    /review's '.nav', which still turns into a horizontal strip on narrow
    screens) keep working unchanged. `guard` now wraps nothing, because there
    are no state-dependent rules left to guard.
    """
    del sel, guard          # nothing here varies by page any more
    return """
#themebtn { margin-bottom:6px; margin-top:auto; }
"""


# The rail's two buttons, rendered identically on every page.
RAIL_BUTTONS_HTML = (
    '<button id="themebtn" class="navbtn" onclick="toggleTheme()">'
    '<span class="ic">◑</span><span id="themelbl">Light</span></button>'
)

# Runs in <head> BEFORE the body paints. Without it the page renders dark for a
# frame and then snaps to light, which reads as a bug. The rail needs no
# equivalent any more: it has exactly one state, so it cannot paint in the
# wrong one.
HEAD_THEME_JS = (
    '<script>(function(){try{var t=localStorage.getItem("theme");'
    'if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();'
    '</script>'
)

# Old name kept so nothing that still imports it breaks.
HEAD_RAIL_THEME_JS = HEAD_THEME_JS

# Shared behaviour. The theme persists under one key, read by every page, so it
# is a single habit rather than a per-page setting. (The rail toggle that used
# to live here was removed with auto-hide — see rail_css.)
SHARED_JS = """
function currentTheme() {
  try { return localStorage.getItem('theme') || 'dark'; } catch (e) { return 'dark'; }
}
function applyTheme() {
  const t = currentTheme();
  document.documentElement.setAttribute('data-theme', t);
  const ic = document.querySelector('#themebtn .ic');
  const lb = document.getElementById('themelbl');
  const b = document.getElementById('themebtn');
  // The control is labelled with what it will DO, not what is showing.
  if (ic) ic.textContent = t === 'light' ? '◕' : '◑';
  if (lb) lb.textContent = t === 'light' ? 'Dark' : 'Light';
  if (b) b.title = t === 'light' ? 'Switch to the dark theme'
                                 : 'Switch to the light theme';
}
function toggleTheme() {
  try { localStorage.setItem('theme', currentTheme() === 'light' ? 'dark' : 'light'); }
  catch (e) {}
  applyTheme();
}
applyTheme();
"""


# ---------------------------------------------------------------- sidebar
# The Scrapper studio's navigation, shared by every page (owner, 2026-10-02:
# "take notes from the scrapper web design and mobile friendliness"):
#   desktop  — a 280px sidebar: brand, items with title + subtitle + badge,
#              a footer with live status; "←" collapses it to 76px icons and
#              the choice is remembered.
#   phones   — (<760px) the sidebar slides in from the left behind a "☰" in a
#              slim top bar, over a dimmed backdrop; every page gets the full
#              width. This replaced the bottom tab bar.
# Pages place their content beside it with margin-left: var(--side-w).
SIDE_CSS = """
:root { --side-w:280px; }
html[data-side="collapsed"] { --side-w:76px; }
.side { position:fixed; left:0; top:0; bottom:0; width:var(--side-w); z-index:200;
  background:var(--side-bg); border-right:1px solid var(--rule);
  display:flex; flex-direction:column;
  transition:width .2s cubic-bezier(.4,0,.2,1), transform .2s ease; }
.side-brand { display:flex; align-items:center; justify-content:space-between;
  gap:8px; padding:18px 18px 16px; border-bottom:1px solid var(--rule); }
.side-brand .bn { display:flex; align-items:center; gap:8px; }
.side-brand .bi { font-size:18px; }
.side-brand .bt { font-weight:800; font-size:16px; letter-spacing:-.02em; color:var(--ink); }
.side-brand .bp { font-size:10px; font-weight:700; color:var(--ok); background:var(--badge-ok-bg);
  padding:2px 6px; border-radius:8px; letter-spacing:.05em; }
.side-brand .bs { font-size:11px; color:var(--ink3); margin-top:2px; }
.side-collapse { padding:5px 9px; background:transparent; border:1px solid var(--rule);
  color:var(--ink3); border-radius:6px; box-shadow:none; font-weight:400; line-height:1; }
.side-nav { flex:1; overflow-y:auto; overflow-x:hidden; padding:14px 10px; display:flex; flex-direction:column; gap:5px; }
.navitem { position:relative; display:flex; align-items:center; gap:12px; width:100%;
  padding:10px 14px; border-radius:8px; border:1px solid transparent; background:transparent;
  color:var(--nav-ink); text-align:left; text-decoration:none; box-shadow:none;
  font-weight:500; cursor:pointer; transition:background .15s, color .15s; }
.navitem:hover:not(:disabled) { background:var(--nav-on-bg); border-color:transparent; color:var(--ink); }
.navitem.active { background:var(--nav-on-bg); border-color:var(--nav-on-edge); color:var(--nav-on-ink); }
.navitem.active::before { content:""; position:absolute; left:-1px; top:9px; bottom:9px; width:3px;
  border-radius:2px; background:var(--ok); }
.navitem .ic { font-size:18px; line-height:1; flex-shrink:0; width:22px; text-align:center; }
.navitem .tx { flex:1; min-width:0; display:flex; flex-direction:column; }
.navitem .tr { display:flex; justify-content:space-between; align-items:center; gap:6px; }
.navitem .tt { font-size:13px; }
.navitem.active .tt { font-weight:700; }
.navitem .st { font-size:11px; color:var(--ink3); margin-top:1px; white-space:nowrap;
  overflow:hidden; text-overflow:ellipsis; }
.navitem .bd { font-size:10px; font-weight:700; padding:1px 6px; border-radius:10px; white-space:nowrap;
  color:var(--ink3); background:var(--badge-muted-bg); }
.navitem .bd.ok { color:var(--ok); background:var(--badge-ok-bg); }
.navitem .bd.warn { color:var(--badge-warn-ink); background:var(--badge-warn-bg); }
.navitem .vdot { position:absolute; left:28px; top:8px; margin:0; }
.side-foot { border-top:1px solid var(--rule); background:var(--side-foot); padding:14px 16px;
  font-size:11px; display:flex; flex-direction:column; gap:9px; }
.side-foot .fr { display:flex; justify-content:space-between; align-items:center; gap:8px; color:var(--ink3); }
.side-foot .fv { color:var(--ink); font-weight:600; font-family:ui-monospace,SFMono-Regular,Menlo,monospace; }
.side-foot .live { color:var(--ok); }
.side-foot #themebtn { padding:4px 9px; font-size:11px; font-weight:600; box-shadow:none;
  display:inline-flex; gap:5px; align-items:center; width:auto; height:auto; }
.side-foot #themebtn .ic { font-size:12px; }
/* collapsed: icons only */
html[data-side="collapsed"] .side-brand { justify-content:center; padding:18px 8px 16px; flex-direction:column; }
html[data-side="collapsed"] .side-brand .bn > :not(.bi),
html[data-side="collapsed"] .side-brand .bs,
html[data-side="collapsed"] .navitem .tx,
html[data-side="collapsed"] .side-foot .fr:not(.keep) { display:none; }
html[data-side="collapsed"] .navitem { justify-content:center; padding:12px 0; }
html[data-side="collapsed"] .side-foot { align-items:center; padding:12px 6px; }
html[data-side="collapsed"] .side-foot .fr.keep { flex-direction:column; gap:8px; }
html[data-side="collapsed"] .side-foot .live { font-size:0; }
html[data-side="collapsed"] .side-foot .live::first-letter { font-size:13px; }
html[data-side="collapsed"] #themelbl { display:none; }
/* phone top bar + backdrop (hidden on desktop) */
.mtop { display:none; }
.side-backdrop { display:none; }
@media (max-width:760px) {
  html:root, html[data-side] { --side-w:0px; }
  .side { width:280px; transform:translateX(-100%); }
  html.side-open .side { transform:none; box-shadow:0 0 40px rgba(0,0,0,.6); }
  html.side-open .side-backdrop { display:block; position:fixed; inset:0; z-index:150;
    background:rgba(0,0,0,.55); }
  /* inside the open drawer always show the full labels */
  html[data-side="collapsed"] .side .navitem .tx,
  html[data-side="collapsed"] .side-brand .bn > :not(.bi),
  html[data-side="collapsed"] .side-brand .bs,
  html[data-side="collapsed"] .side-foot .fr { display:flex; }
  html[data-side="collapsed"] .side-brand .bs { display:block; }
  html[data-side="collapsed"] .navitem { justify-content:flex-start; padding:10px 14px; }
  html[data-side="collapsed"] .side-brand { flex-direction:row; justify-content:space-between; padding:18px 18px 16px; }
  .mtop { display:flex; position:sticky; top:0; z-index:100; align-items:center; gap:10px;
    padding:calc(8px + env(safe-area-inset-top, 0px)) 10px 8px; background:var(--top-bg);
    border-bottom:1px solid var(--rule); }
  .mtop .menu { font-size:20px; padding:4px 12px; line-height:1; min-height:38px; }
  .mtop .mt { font-weight:700; font-size:15px; color:var(--ink); }
  .mtop .ms { margin-left:auto; font-size:11px; color:var(--ok); white-space:nowrap; }
  .navitem { min-height:52px; }
}
"""

# Before paint: restore the collapsed choice (default collapsed on narrow
# desktops, where 280px would crowd the board) so the sidebar never jumps.
HEAD_SIDE_JS = (
    '<script>(function(){try{var s=localStorage.getItem("side");'
    'if(!s&&innerWidth<1100&&innerWidth>=760)s="collapsed";'
    'if(s==="collapsed")document.documentElement.setAttribute("data-side","collapsed");'
    '}catch(e){}})();</script>'
)

SIDE_JS = """
function sideIsPhone() { return window.matchMedia('(max-width: 760px)').matches; }
function sideOpen() { document.documentElement.classList.add('side-open'); }
function sideClose() { document.documentElement.classList.remove('side-open'); }
function sideToggle() {
  if (sideIsPhone()) { sideClose(); return; }
  var h = document.documentElement, c = h.getAttribute('data-side') === 'collapsed';
  if (c) h.removeAttribute('data-side'); else h.setAttribute('data-side', 'collapsed');
  try { localStorage.setItem('side', c ? 'open' : 'collapsed'); } catch (e) {}
  var b = document.querySelector('.side-collapse');
  if (b) { b.textContent = c ? '\\u2190' : '\\u2192'; b.title = c ? 'Collapse sidebar' : 'Expand sidebar'; }
}
(function () {
  var b = document.querySelector('.side-collapse');
  if (b && document.documentElement.getAttribute('data-side') === 'collapsed') {
    b.textContent = '\\u2192'; b.title = 'Expand sidebar';
  }
  // On a phone, choosing anything in the menu closes it.
  document.addEventListener('click', function (e) {
    if (sideIsPhone() && e.target.closest && e.target.closest('.side .navitem')) sideClose();
  });
})();
"""


def _esc(s):
    import html as _h
    return _h.escape(str(s), quote=True)


def sidebar_html(items, foot_rows=(), status="● live"):
    """items: dicts with icon, title, sub, and either href or onclick
    (+ optional d=drawer name, badge, badge_kind 'ok'|'warn'|'', active,
    title_attr). foot_rows: (label, value) pairs for the footer."""
    out = ['<div class="side-backdrop" onclick="sideClose()"></div>',
           '<div class="mtop"><button class="menu" onclick="sideOpen()" '
           'aria-label="Open menu">☰</button><div class="mt">🎬 Recap Studio</div>'
           f'<span class="ms">{_esc(status)}</span></div>',
           '<aside class="side" id="rail">',
           '<div class="side-brand"><div><div class="bn"><span class="bi">🎬</span>'
           '<span class="bt">Recap</span><span class="bp">STUDIO</span></div>'
           '<div class="bs">Manhwa recap pipeline</div></div>'
           '<button class="side-collapse" onclick="sideToggle()" '
           'title="Collapse sidebar">←</button></div>',
           '<nav class="side-nav">']
    for it in items:
        cls = "navbtn navitem" + (" active" if it.get("active") else "")
        attrs = f' class="{cls}"'
        if it.get("d"):
            attrs += f' data-d="{_esc(it["d"])}"'
        if it.get("v"):
            attrs += f' data-v="{_esc(it["v"])}"'
        if it.get("title_attr"):
            attrs += f' title="{_esc(it["title_attr"])}"'
        badge = ""
        if it.get("badge") not in (None, ""):
            badge = (f'<span class="bd {_esc(it.get("badge_kind", ""))}">'
                     f'{_esc(it["badge"])}</span>')
        inner = (f'<span class="ic">{it["icon"]}</span><span class="tx">'
                 f'<span class="tr"><span class="tt">{_esc(it["title"])}</span>{badge}</span>'
                 f'<span class="st">{_esc(it.get("sub", ""))}</span></span>')
        if it.get("href"):
            out.append(f'<a href="{_esc(it["href"])}"{attrs}>{inner}</a>')
        else:
            out.append(f'<button onclick="{_esc(it.get("onclick", ""))}"{attrs}>{inner}</button>')
    out.append('</nav><div class="side-foot">')
    for label, value in foot_rows:
        out.append(f'<div class="fr"><span>{_esc(label)}</span>'
                   f'<span class="fv">{_esc(value)}</span></div>')
    out.append(f'<div class="fr keep"><span class="live">{_esc(status)}</span>'
               f'{RAIL_BUTTONS_HTML}</div></div></aside>')
    return "".join(out)


# The pages that list the app's sections, in one place so both pages agree.
def nav_items(page, board_badge=None):
    """page: 'board' or 'review'. On the board, panels open in place; from
    /review they link back to the board with ?open=<panel>."""
    def item(icon, title, sub, d=None, **kw):
        if page == "board" and d:
            kw.setdefault("onclick", f"toggleDrawer('{d}')")
            kw["d"] = d
        elif d:
            kw.setdefault("href", f"/storyboard?open={d}")
        return dict(icon=icon, title=title, sub=sub, **kw)
    board = dict(icon="🎬", title="Board", sub="Script, panels & timing",
                 active=(page == "board"), badge=board_badge, badge_kind="ok")
    if page == "board":
        board["onclick"] = "toggleDrawer('board')"
        board["v"] = "board"
    else:
        board["href"] = "/storyboard"
    return [
        item("🔗", "Ingest", "Paste a chapter link", d="ingest"),
        board,
        item("🛡", "Check", "Story mistakes", d="validate"),
        item("📤", "Exports", "Finished videos", d="exports"),
        dict(icon="📺", title="Review & Publish", sub="Approve, then post",
             href="/review", active=(page == "review")),
        item("📚", "Projects", "Every chapter", d="projects"),
        item("📡", "Tracker", "Series & new chapters", d="tracker"),
        item("📋", "Logs & Activity", "Jobs, usage & what changed", d="logs",
             title_attr="Every job with Stop, API usage, and every change made to the system with its evidence"),
    ]


# ---------------------------------------------------------------- panels
# The Scrapper studio's Panel: a card whose title sits in its own header bar
# (uppercase, muted, on the second surface), content padded below. Used for
# every section page, the board and every /review card.
PANEL_CSS = """
.panel { background:var(--panel); border:1px solid var(--rule); border-radius:10px;
  margin:0 0 16px; overflow:hidden; }
.panel-h { display:flex; align-items:center; gap:10px; padding:10px 14px;
  background:var(--panel2); border-bottom:1px solid var(--rule); }
.panel-h h2 { margin:0; font-size:12px; text-transform:uppercase; letter-spacing:.8px;
  color:var(--ink3); font-weight:700; }
.panel-h .r { margin-left:auto; }
details.legend { padding:10px 14px; border-bottom:1px solid var(--rule); }
details.legend summary { cursor:pointer; color:var(--ink3); font-size:12px; font-weight:600; }
details.legend p.meta { margin:8px 0 0; }
/* segmented tabs inside a page (Logs: Jobs | What changed; Tracker views) */
.segtabs { display:flex; gap:6px; flex-wrap:wrap; margin:0 0 12px; }
.segtabs button { font-size:12px; padding:5px 12px; background:transparent; box-shadow:none; font-weight:600; }
.segtabs button.on { background:var(--btn); border-color:var(--btn-edge); color:var(--ink); }
"""

# Everything running on the server, with Stop — at the top of every page,
# like the Scrapper studio's JobsBar. Reads the same two feeds the Logs page
# uses and stops through the same control endpoint.
JOBS_CSS = """
.jobsbar { display:flex; flex-direction:column; gap:6px; }
.jobsbar:empty { display:none; }
.jobrow { display:flex; align-items:center; gap:10px; flex-wrap:wrap; padding:8px 12px;
  border-radius:8px; background:var(--job-bg); border:1px solid var(--rule); font-size:12px; }
.jobrow.err { background:var(--job-err-bg); }
.jobrow .jl { color:var(--job-ink); font-weight:700; }
.jobrow.err .jl { color:var(--job-err-ink); }
.jobrow .jm { color:var(--ink3); flex:1; min-width:120px; }
.jobrow .jp { height:4px; flex-basis:100%; background:rgba(255,255,255,.08); border-radius:2px; overflow:hidden; }
.jobrow .jp > i { display:block; height:100%; background:var(--ok); }
.jobrow button { font-size:11px; padding:3px 12px; }
"""

JOBS_JS = """
(function () {
  // 2026-10-04: one light feed (/api/jobsbar: in-memory only) instead of two
  // full job listings every 4 s; readable names; ▶ for jobs waiting on a
  // restart or the spend cap; two-tap Stop (confirm() can be silently blocked
  // in in-app browsers — the Scrapper lesson).
  var ICON = { ingest: '📥', autopilot: '🤖', finalize: '🎬', render: '🎬', publish: '📺',
               research: '📖', validate: '🛡', lab: '🧪' };
  function esc(x) { return String(x == null ? '' : x).replace(/[&<>"]/g, function (c) {
    return { '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]; }); }
  function post(u, body) {
    return fetch(u, { method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body) }).then(load, load);
  }
  function paint(jobs) {
    var box = document.getElementById('jobsbar');
    if (!box || box.querySelector('[data-armed]')) return;
    var html = jobs.map(function (j) {
      var wait = j.status === 'budget_paused' || j.status === 'interrupted';
      var el = j.elapsed >= 60 ? Math.round(j.elapsed / 60) + ' min' : (j.elapsed || 0) + ' s';
      return '<div class="jobrow' + (wait ? ' err' : '') + '"><span class="jl">' + (ICON[j.kind] || '⚙') + ' ' + esc(j.name) +
        '</span><span class="jm">' + esc(String(j.status).replace('_', ' ')) + (j.stage ? ' · ' + esc(j.stage) : '') +
        (j.msg ? ' · ' + esc(j.msg) : '') + (j.pct != null ? ' · ' + j.pct + '%' : '') + ' · ' + el + '</span>' +
        (wait ? '<button data-job="' + esc(j.id) + '" onclick="jobsResume(this.dataset.job)">▶ resume</button>'
              : '<button data-job="' + esc(j.id) + '" onclick="jobsPause(this.dataset.job)">⏸</button>') +
        '<button class="danger" data-job="' + esc(j.id) + '" onclick="jobsStop(this)">⏹ Stop</button>' +
        (j.pct != null ? '<span class="jp"><i style="width:' + j.pct + '%"></i></span>' : '') + '</div>';
    }).join('');
    if (html !== box.dataset.last) { box.innerHTML = html; box.dataset.last = html; }
  }
  function load() {
    if (document.hidden) return;
    fetch('/api/jobsbar', { cache: 'no-store' }).then(function (r) { return r.ok ? r.json() : null; })
      .then(function (d) { if (d) paint(d.jobs || []); }).catch(function () {});
  }
  window.jobsBarLoad = load;
  window.jobsPause = function (id) { post('/api/jobs/control', { job_id: id, action: 'pause' }); };
  window.jobsResume = function (id) { post('/api/jobs/resume', { job_id: id }); };
  window.jobsStop = function (btn) {
    if (btn.dataset.armed) {
      delete btn.dataset.armed; btn.textContent = '…';
      post('/api/jobs/control', { job_id: btn.dataset.job, action: 'stop' });
      return;
    }
    btn.dataset.armed = '1'; btn.textContent = 'tap again to stop';
    setTimeout(function () { if (btn.dataset.armed) { delete btn.dataset.armed; btn.textContent = '⏹ Stop'; } }, 3000);
  };
  load();
  setInterval(load, 4000);
  document.addEventListener('visibilitychange', load);
})();
"""
