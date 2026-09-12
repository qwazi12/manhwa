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
  --bg:#0b0e14; --panel:#141824; --panel2:#1b2130; --rule:#2e3648;
  --ink:#f2f4fa; --ink2:#c3cbdb; --ink3:#8e97ad;
  /* Signal colours. Deliberately vivid: the previous set sat so close to the
     panel that a primary action was indistinguishable from a label. */
  --accent:#4cc9ff; --ok:#2be08a; --warn:#ffb224; --bad:#ff5470;
  --ai:#c07cff;
  /* CTAs are FILLED with the signal colour and carry dark ink, which is what
     makes them read as buttons at a glance instead of as bordered text. */
  --cta:#00d5ff;      --cta-ink:#04222b;   --cta-hover:#5ce4ff;
  --cta2:#273044;     --cta2-ink:#e9eefc;  --cta2-hover:#323d55;
  --ai-cta:#b463ff;   --ai-ink:#1b0733;    --ai-hover:#c88bff;
  --ok-cta:#2be08a;   --ok-cta-ink:#02261a;
  --bad-cta:#ff5470;  --bad-cta-ink:#2b0510;
  --sel:#00d5ff;                      /* active selection */
  --applied:#2be08a;                  /* a value that has been applied */
  --glow:0 0 0 1px rgba(0,213,255,.35), 0 4px 18px -4px rgba(0,213,255,.55);
  --glow-ai:0 0 0 1px rgba(180,99,255,.35), 0 4px 18px -4px rgba(180,99,255,.55);
  --btn:#232c3d; --btn-hover:#2d3850; --btn-edge:#47536e; --btn-ink:#f2f4fa;
  --focus:#8fe4ff; --shadow:rgba(0,0,0,.5);
  --accent-ink:#04222b;
  --sa-bg:#10243f;    --sa-ink:#8ecbff;
  --fold-bg:#2e2712;  --fold-ink:#ffd98a;
  --omit-bg:#36171f;  --omit-ink:#ffa8b6;
  --gray-bg:#1e2433;  --gray-ink:#9aa3b8;
  --seg-bg:#151b27;   --seg-rule:#2b3446;
  --okb-bg:#0f3326;   --okb-ink:#6bf0b3;
  --warnb-bg:#38290f; --warnb-ink:#ffd07a;
  --badb-bg:#3a1420;  --badb-ink:#ffabb9;
  --tall-bg:#261c40;  --tall-ink:#d0b4ff;
  --tight-bg:#3c2210; --tight-ink:#ffbc85;
}
:root[data-theme="light"] {
  color-scheme: light;
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
    """Retract rules for the rail, whose selector differs per page.

    Retracted the rail is a 14px sliver. Hover OR keyboard focus reopens it
    OVER the content rather than reflowing the page, so nothing shifts under
    the pointer as it opens.

    `guard` wraps ONLY the retract rules in a media query — /review turns its
    rail into a horizontal strip on narrow screens, where there is nothing to
    retract and no hover to do it with. The reduced-motion query is emitted
    outside that wrapper, because @media cannot be nested in plain CSS.
    """
    rules = """
%(s)s { transition:width .16s ease; }
body.railoff %(s)s { width:14px; }
body.railoff %(s)s > * { opacity:0; pointer-events:none; transition:opacity .12s ease; }
body.railoff %(s)s::after { content:'›'; position:absolute; top:50%%; left:0;
  width:14px; margin-top:-10px; text-align:center; color:var(--accent); font-size:14px; }
body.railoff %(s)s:hover, body.railoff %(s)s:focus-within { width:64px; z-index:80;
  box-shadow:4px 0 18px var(--shadow); }
body.railoff %(s)s:hover > *, body.railoff %(s)s:focus-within > * {
  opacity:1; pointer-events:auto; }
body.railoff %(s)s:hover::after, body.railoff %(s)s:focus-within::after { content:none; }
""" % {"s": sel}
    if guard:
        rules = "@media %s {%s}\n" % (guard, rules)
    return rules + """
#railpin, #themebtn { margin-bottom:6px; }
#railpin { margin-top:auto; }
@media (prefers-reduced-motion: reduce) {
  %(s)s, body.railoff %(s)s > * { transition:none; }
}
""" % {"s": sel}


# The rail's two buttons, rendered identically on every page.
RAIL_BUTTONS_HTML = (
    '<button id="themebtn" class="navbtn" onclick="toggleTheme()">'
    '<span class="ic">◑</span><span id="themelbl">Light</span></button>\n'
    '  <button id="railpin" class="navbtn" onclick="toggleRail()">'
    '<span class="ic">«</span><span id="railpinlbl">Hide</span></button>'
)

# Runs in <head> BEFORE the body paints. Without it the page renders dark for a
# frame and then snaps to light, which reads as a bug.
HEAD_THEME_JS = (
    '<script>(function(){try{var t=localStorage.getItem("theme");'
    'if(t)document.documentElement.setAttribute("data-theme",t);}catch(e){}})();'
    '</script>'
)

# Shared behaviour. Both toggles persist under one key each, read by every page,
# so the rail and the theme are single habits rather than per-page settings.
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
function applyRail() {
  // Auto-hide is the DEFAULT: the rail sits as a sliver and opens on hover or
  // keyboard focus, rather than needing to be retracted by hand. Pinning it
  // open is still one click, and that choice is remembered.
  let off = true;
  try {
    const v = localStorage.getItem('railoff');
    if (v !== null) off = v === '1';
  } catch (e) {}
  document.body.classList.toggle('railoff', off);
  const ic = document.querySelector('#railpin .ic');
  const lb = document.getElementById('railpinlbl');
  const p = document.getElementById('railpin');
  if (ic) ic.textContent = off ? '»' : '«';
  if (lb) lb.textContent = off ? 'Pin' : 'Hide';
  if (p) p.title = off ? 'Keep the sidebar open'
                       : 'Retract the sidebar to a sliver';
}
function toggleRail() {
  try {
    localStorage.setItem('railoff',
      document.body.classList.contains('railoff') ? '0' : '1');
  } catch (e) {}
  applyRail();
}
applyTheme();
applyRail();
"""
