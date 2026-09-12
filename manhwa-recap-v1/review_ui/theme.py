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
  --bg:#0e1016; --panel:#161923; --panel2:#1b1f2a; --rule:#303646;
  --ink:#eef0f6; --ink2:#bcc3d2; --ink3:#8b93a6;
  --accent:#6f9bff; --ok:#3fcd88; --warn:#efb44a; --bad:#f56b77;
  /* Controls get their own surface so they never melt into the panel. */
  --btn:#28303f; --btn-hover:#333c4e; --btn-edge:#4b5670; --btn-ink:#eef0f6;
  --focus:#8fb4ff; --shadow:rgba(0,0,0,.45);
  --accent-ink:#0b1020;
  /* Tinted rows and badges, as bg/ink PAIRS so contrast stays deliberate. */
  --sa-bg:#14203a;    --sa-ink:#a8c8ff;
  --fold-bg:#2a2413;  --fold-ink:#f0d28a;
  --omit-bg:#31181c;  --omit-ink:#ffa8b0;
  --gray-bg:#1c202a;  --gray-ink:#99a1b3;
  --seg-bg:#141a24;   --seg-rule:#2b3342;
  --okb-bg:#133023;   --okb-ink:#8fe5b6;
  --warnb-bg:#312513; --warnb-ink:#f2cd8a;
  --badb-bg:#33181d;  --badb-ink:#ffabb3;
  --tall-bg:#231d3b;  --tall-ink:#c5b4ff;
  --tight-bg:#352012; --tight-ink:#ffbb8a;
}
:root[data-theme="light"] {
  color-scheme: light;
  --bg:#f4f6f9; --panel:#ffffff; --panel2:#eceff5; --rule:#c9d0dd;
  --ink:#151821; --ink2:#49515f; --ink3:#5c6474;
  --accent:#2b5fd9; --ok:#12804c; --warn:#8a5a00; --bad:#c02a36;
  --btn:#ffffff; --btn-hover:#eef1f7; --btn-edge:#a9b2c4; --btn-ink:#151821;
  --focus:#2b5fd9; --shadow:rgba(16,24,40,.16);
  --accent-ink:#ffffff;
  --sa-bg:#e7f0ff;    --sa-ink:#17458f;
  --fold-bg:#fdf3d8;  --fold-ink:#6f5100;
  --omit-bg:#fce9e9;  --omit-ink:#93242c;
  --gray-bg:#eceef3;  --gray-ink:#5b6271;
  --seg-bg:#f7f9fc;   --seg-rule:#dde3ed;
  --okb-bg:#e0f4e9;   --okb-ink:#13653d;
  --warnb-bg:#faefd5; --warnb-ink:#74510a;
  --badb-bg:#fbe8ea;  --badb-ink:#9c2730;
  --tall-bg:#ece6ff;  --tall-ink:#452a96;
  --tight-bg:#ffe9dc; --tight-ink:#8f3a0f;
}
"""

# ---------------------------------------------------------------- controls
# One rule set for every clickable thing on both pages. `button` is styled
# element-wide so a control added later is correct without being remembered.
CONTROLS_CSS = """
button, .cropact {
  font:inherit; background:var(--btn); color:var(--btn-ink);
  border:1px solid var(--btn-edge); border-radius:6px; cursor:pointer;
  box-shadow:0 1px 0 var(--shadow); transition:background .12s, border-color .12s;
}
button:hover:not(:disabled), .cropact:hover {
  background:var(--btn-hover); border-color:var(--accent);
}
button:disabled { opacity:.45; cursor:not-allowed; box-shadow:none; }
button.primary, .drawer button.primary {
  background:var(--accent); border-color:var(--accent); color:var(--accent-ink);
  font-weight:700;
}
button.primary:hover:not(:disabled) { filter:brightness(1.1); }
/* Keyboard users get the same affordance the mouse gets. */
button:focus-visible, .cropact:focus-visible, a:focus-visible,
input:focus-visible, textarea:focus-visible, select:focus-visible,
.navbtn:focus-visible {
  outline:2px solid var(--focus); outline-offset:2px;
}
input[type=checkbox] { accent-color:var(--accent); }
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
