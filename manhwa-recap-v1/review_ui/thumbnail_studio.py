"""Series Thumbnail Copilot — consistent per-series packaging, per-chapter hooks.

The product rule this whole module exists to enforce:

    KEEP THE SAME SERIES VISUAL DNA, UPDATE ONLY THE CHAPTER HOOK.

So there are two persistence layers with different lifetimes:

  * a STYLE PACK, stored per SERIES (not per project), holding palette, badge
    style, composition family, typography and the anchor artwork. Once approved
    it is inherited by every later chapter of that manhwa.
  * CONCEPTS, stored per EXPORT, holding the chapter-specific focal panel,
    chapter number and hook text.

Composition is deterministic: thumbnails are assembled with Pillow from the
project's OWN ingested panel crops — the same artwork already in the rendered
video — plus a badge and optional short text. Nothing is drawn or invented, and
no external image model is involved. That keeps output grounded in the chapter,
makes the whole pipeline unit-testable, and costs nothing per generation.
"""
import colorsys
import glob
import json
import os
import re
import time

W, H = 1280, 720                    # YouTube's recommended custom thumbnail
MIN_W = 640                         # YouTube's hard minimum width
JPEG_Q = 88                         # ~150-400KB at 1280x720, far under 2MB
STYLES_DIR = "_thumbstyles"         # per-SERIES, shared across chapters
CONCEPTS_NAME = "thumb_concepts.json"   # per-export, inside the project
STYLE_VERSION = 1

CONCEPT_TYPES = ("character", "action", "mystery", "transformation", "emotion")

# Hook text must stay readable at 168px wide in a sidebar, hence the hard cap.
# Spec 07 §4.1: 1-4 words.
MAX_HOOK_WORDS = 4
MAX_HOOK_CHARS = 28

# Words that make a weak overlay — they carry no hook on their own.
_WEAK = set("""a an the and or but of to in on at by for with from as is are was
were be this that his her their its it he she they you your my our who what
when where how chapter part recap manhwa""".split())


# --------------------------------------------------------------- hook text
# Spec 07 (docs/audit/07_THUMBNAIL_RECIPE.md) §1 "Text size floor": cap height
# on the 1280-wide canvas is set by word count and never goes below 84 px; if
# the hook does not fit at the floor, words are dropped — never shrunk further.
CAP_BY_WORDS = {1: 180, 2: 132, 3: 108, 4: 90}
CAP_FLOOR = 84
STROKE_PCT, STROKE_MIN = 0.08, 9
HOOK_FILL = (255, 212, 0)            # #FFD400 — 8/8 top performers
# "line gap 0.92 x cap height": read as line pitch = 0.92 x the font SIZE (tight
# leading). The literal reading (gap of 0.92 x cap between lines) cannot fit two
# lines inside the spec's 222 px text zone at any cap height >= 84.
LINE_PITCH = 0.92
MAX_LINES = 2


def cap_height_for(words):
    """Cap height (px, on 1280x720) for a hook of `words` words (spec 07 §1)."""
    if words <= 0:
        return 0
    return max(CAP_FLOOR, CAP_BY_WORDS.get(min(int(words), 4), CAP_FLOOR))


def stroke_for(cap):
    return max(STROKE_MIN, int(round(STROKE_PCT * cap)))


_CAP_RATIO = {}


def font_for_cap(cap):
    """The hook font sized so its CAPITALS measure `cap` px tall."""
    f100 = _font(100)
    key = getattr(f100, "path", "default")
    if key not in _CAP_RATIO:
        b = f100.getbbox("H")
        _CAP_RATIO[key] = max(0.3, (b[3] - b[1]) / 100.0)
    return _font(max(8, int(round(cap / _CAP_RATIO[key]))))


def hook_layout(text, max_w, max_h, measure=None):
    """Fit a 1-4 word hook into a box at the spec's cap heights.

    Returns {"lines", "cap", "font", "stroke", "words_used", "dropped"} or None.
    Order: the word-count cap height on one line, then two lines; if it still
    does not fit, step the cap down — but never below CAP_FLOOR — and only then
    drop the last word."""
    words = [w for w in (text or "").upper().split() if w][:MAX_HOOK_WORDS]
    total = len(words)
    while words:
        top = cap_height_for(len(words))
        for cap in range(top, CAP_FLOOR - 1, -2):
            f = font_for_cap(cap)
            st = stroke_for(cap)
            pitch = int(round(LINE_PITCH * f.size))
            tl = (lambda t: (measure or f.getlength)(t) + 2 * st) if measure is None else \
                (lambda t: measure(t, f) + 2 * st)
            options = [[" ".join(words)]]
            if len(words) > 1:
                options += [[" ".join(words[:i]), " ".join(words[i:])] for i in range(1, len(words))]
                options[1:] = sorted(options[1:], key=lambda ls: max(tl(l) for l in ls))
            for lines in options:
                h = cap + (len(lines) - 1) * pitch + 2 * st
                if len(lines) <= MAX_LINES and max(tl(l) for l in lines) <= max_w and h <= max_h:
                    return {"lines": lines, "cap": cap, "font": f, "stroke": st, "pitch": pitch,
                            "words_used": len(words), "dropped": total - len(words)}
        words = words[:-1]                     # truncate — never shrink past the floor
    return None


def draw_hook(img, layout, x, y_bottom):
    """Yellow #FFD400 caps, pure-black stroke OUTSIDE the glyph, drop shadow
    (#000 @55%, offset (0,4), blur 8). Text sits on its bottom edge at y_bottom.
    Returns the drawn block's bbox (x0, y0, x1, y1)."""
    from PIL import Image, ImageDraw, ImageFilter
    f, st, cap, pitch = layout["font"], layout["stroke"], layout["cap"], layout["pitch"]
    lines = layout["lines"]
    asc_to_cap = f.getbbox("H")[1]             # empty space above the capitals
    block_h = cap + (len(lines) - 1) * pitch
    y_top = y_bottom - block_h - st
    shadow = Image.new("RGBA", img.size, (0, 0, 0, 0))
    sd = ImageDraw.Draw(shadow)
    d = ImageDraw.Draw(img)
    x1 = x
    for i, line in enumerate(lines):
        ty = y_top + i * pitch - asc_to_cap
        sd.text((x, ty + 4), line, font=f, fill=(0, 0, 0, 140), stroke_width=st, stroke_fill=(0, 0, 0, 140))
        x1 = max(x1, x + f.getlength(line) + 2 * st)
    img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(8)))
    for i, line in enumerate(lines):
        ty = y_top + i * pitch - asc_to_cap
        # Pillow draws the stroke around the glyph (outside + inside); drawing the
        # fill again on top keeps the yellow at full width, so the stroke reads as
        # OUTSIDE the letterform.
        d.text((x + st, ty), line, font=f, fill=(0, 0, 0, 255), stroke_width=st, stroke_fill=(0, 0, 0, 255))
        d.text((x + st, ty), line, font=f, fill=HOOK_FILL + (255,))
    return (x, y_top - st, int(x1), y_bottom)


# --------------------------------------------------------------- typography
# Spec 07 §1 / A8: a CONDENSED HEAVY sans only (Anton, Bebas Neue, Oswald Bold,
# Archivo Black) — never decorative, serif or the series' logo font. Anton is
# vendored with its SIL OFL licence (review_ui/fonts/), so the container and a
# dev Mac resolve the SAME face; system fonts are a last resort and are logged.
_HERE = os.path.dirname(os.path.abspath(__file__))
CONDENSED_FONTS = [
    os.path.join(_HERE, "fonts", "Anton-Regular.ttf"),
    "/usr/share/fonts/truetype/anton/Anton-Regular.ttf",
    "/usr/share/fonts/truetype/bebas-neue/BebasNeue-Regular.ttf",
    "/usr/share/fonts/truetype/oswald/Oswald-Bold.ttf",
    "/usr/share/fonts/truetype/archivo-black/ArchivoBlack-Regular.ttf",
]
CONDENSED_FAMILIES = ("Anton", "Bebas Neue", "Oswald", "Archivo Black")
_FALLBACK_FONTS = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/System/Library/Fonts/Supplemental/Impact.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]
_FONT_LOGGED = {"path": None}


def font_path():
    """The face every thumbnail uses: the first condensed heavy sans found."""
    for p in CONDENSED_FONTS + _FALLBACK_FONTS:
        if os.path.exists(p):
            return p
    return ""


def _font(size, bold=True):
    """The hook/badge face at `size` px. Logs the resolved path once per
    process (and a WARNING on a non-condensed fallback) so a silent fall back to
    DejaVu can never go unnoticed again."""
    from PIL import ImageFont
    p = font_path()
    if p and _FONT_LOGGED["path"] != p:
        _FONT_LOGGED["path"] = p
        cond = p in CONDENSED_FONTS
        print(json.dumps({"service": "thumbnail_studio", "event": "font_resolved", "path": p,
                          "condensed": cond, "level": "info" if cond else "warning"}), flush=True)
    if p:
        try:
            return ImageFont.truetype(p, size)
        except Exception:
            pass
    return ImageFont.load_default()


def font_available():
    """True only when the CONDENSED heavy face is in use (not just any TTF)."""
    from PIL import ImageFont
    f = _font(40)
    if isinstance(f, ImageFont.ImageFont):
        return False
    try:
        fam = f.getname()[0]
    except Exception:
        return False
    return any(fam.startswith(c) for c in CONDENSED_FAMILIES)


# ------------------------------------------------------------------ palette
def _srgb_sort_key(rgb):
    r, g, b = [c / 255.0 for c in rgb]
    h, l, s = colorsys.rgb_to_hls(r, g, b)
    return (s * 0.6 + (0.5 - abs(l - 0.5)) * 0.8)   # favour vivid, mid-light


def _vivify(rgb):
    """Keep the series' hue, force it bright enough to be a badge.

    Manhwa pages are mostly dark ink, so the most "colourful" pixel is often a
    muted navy. Used raw it makes a badge that disappears. Lifting saturation
    and lightness preserves the series' identity while guaranteeing the chapter
    number is the thing the eye lands on.
    """
    r, g, b = [c / 255.0 for c in rgb]
    h, l, sat = colorsys.rgb_to_hls(r, g, b)
    l = min(0.62, max(0.46, l * 1.9))
    sat = min(1.0, max(0.72, sat * 1.9))
    r, g, b = colorsys.hls_to_rgb(h, l, sat)
    return (int(r * 255), int(g * 255), int(b * 255))


def extract_palette(paths, k=6):
    """Dominant colours from the series' own artwork.

    Derived rather than chosen so each manhwa gets its own identity: a
    blue-lit sci-fi series and a warm historical one should not share a
    thumbnail palette just because they share a tool.
    """
    from PIL import Image
    tally = {}
    for p in paths[:6]:
        try:
            im = Image.open(p).convert("RGB")
            im.thumbnail((160, 160))
            q = im.quantize(colors=k, method=Image.Quantize.FASTOCTREE)
            pal = q.getpalette() or []
            for count, index in (q.getcolors() or []):
                rgb = tuple(pal[index * 3: index * 3 + 3])
                if len(rgb) == 3:
                    tally[rgb] = tally.get(rgb, 0) + count
        except Exception:
            continue
    if not tally:
        return {"bg": (14, 16, 22), "accent": (0, 213, 255),
                "ink": (255, 255, 255), "swatches": []}
    ranked = sorted(tally, key=lambda c: -tally[c])[:10]
    accent = _vivify(max(ranked, key=_srgb_sort_key))
    darks = sorted(ranked, key=lambda c: sum(c))
    bg = darks[0]
    if sum(bg) > 240:                       # never a pale ground: text must sit on it
        bg = tuple(int(c * 0.32) for c in bg)
    return {"bg": bg, "accent": accent, "ink": (255, 255, 255),
            "swatches": [list(c) for c in ranked[:5]]}


def _lum(rgb):
    return (0.2126 * rgb[0] + 0.7152 * rgb[1] + 0.0722 * rgb[2]) / 255.0


def readable_ink(bg):
    """Text colour that survives on this ground — checked, not assumed."""
    return (17, 17, 17) if _lum(bg) > 0.55 else (255, 255, 255)


# ------------------------------------------------------------ source images
def cover_file(pdir):
    """This chapter's copy of the series' real cover, or ""."""
    c = sorted(glob.glob(os.path.join(pdir, "pages", COVER_NAME + ".*")))
    return c[0] if c else ""


def cover_candidates(pdir):
    """The series anchor: the source pages, earliest first.

    Page 1 of a scanlated chapter is the title/cover page, which is why it is
    preferred — it is the most recognisable single image a series has.
    """
    cover = sorted(glob.glob(os.path.join(pdir, "pages", COVER_NAME + ".*")))
    pages = sorted(p for p in
                   glob.glob(os.path.join(pdir, "pages", "*.webp")) +
                   glob.glob(os.path.join(pdir, "pages", "*.jpg")) +
                   glob.glob(os.path.join(pdir, "pages", "*.png"))
                   if COVER_NAME not in os.path.basename(p))
    # A real cover always wins: page 1 of a chapter is a story page, not the
    # series' canonical artwork, and using it made the anchor look arbitrary.
    return (cover + pages)[:3]


def panel_paths(pdir):
    return sorted(glob.glob(os.path.join(pdir, "crops", "*.png")))


# ------------------------------------------------------- focal panel choice
_ACTION = ("fight", "strike", "slash", "blast", "clash", "attack", "explos",
           "blood", "sword", "punch", "battle", "charge", "kill", "duel")
_EMOTION = ("tears", "cry", "scream", "shock", "smile", "grin", "rage", "fear",
            "despair", "glare", "stare", "weep", "laugh")
_REVEAL = ("reveal", "glow", "aura", "transform", "awaken", "power", "light",
           "rises", "appears", "emerges", "throne", "crown")


def score_panel(desc, seg=None):
    """How well one panel would work AS A THUMBNAIL — not as a story beat.

    Favours a single close subject on a wide-ish frame, because that is what
    survives being shrunk to a sidebar. A tall scroll-strip panel is usually a
    poor thumbnail however good the moment is.
    """
    text = ((desc.get("visual_description") or "") + " " +
            (desc.get("ocr_text") or "")).lower()
    w = desc.get("width") or 0
    h = desc.get("height") or 0
    s = {"action": 0, "emotion": 0, "reveal": 0, "shape": 0, "subject": 0}
    s["action"] = min(24, sum(6 for k in _ACTION if k in text))
    s["emotion"] = min(18, sum(6 for k in _EMOTION if k in text))
    s["reveal"] = min(18, sum(6 for k in _REVEAL if k in text))
    if w and h:
        ar = w / float(h)
        # 16:9 is ideal; punish tall strips hard, mild penalty for very wide
        s["shape"] = int(max(0, 22 - abs(ar - 1.78) * 11))
    # A close subject reads at small size; a crowd does not.
    if re.search(r"\b(close[- ]?up|face|eyes|portrait|looking|stares?)\b", text):
        s["subject"] += 12
    if re.search(r"\b(crowd|many|group of|several|background characters)\b", text):
        s["subject"] -= 8
    if seg is not None and seg.get("user_included"):
        s["subject"] += 6           # it is actually in the video
    total = max(0, sum(s.values()))
    return {"total": total, **s}


def _resolve_panel(pdir, file_field, pid):
    """descriptions.json stores a BARE FILENAME ("page001_panel_001.png"), not
    a path, so testing it directly silently rejected every panel. Try it inside
    the project's crops dir, then fall back to the panel id."""
    for cand in ([file_field] if file_field and os.path.isabs(file_field) else []) + \
                ([os.path.join(pdir, "crops", os.path.basename(file_field))]
                 if file_field else []) + \
                [os.path.join(pdir, "crops", "%s.png" % pid)]:
        if cand and os.path.exists(cand):
            return cand
    return None


# Never a thumbnail (image check, panel_stats.classify): speech-bubble cards,
# slivers/blank strips, credits pages.
_NOT_THUMB_ROLES = ("bubble", "fragment", "credits")


def _not_thumbnail(d):
    """Why a panel can never be the picture, or '' — the image check's roles
    plus near-black / flat images it measured."""
    if d.get("role") in _NOT_THUMB_ROLES:
        return d["role"]
    pix = d.get("pix") or {}
    if pix:
        if (pix.get("luma") is not None and pix["luma"] < 22) or \
                (pix.get("std") is not None and pix["std"] < 12):
            return "blank"
    return ""


def lead_names(bible):
    """Names and aliases of the series' lead(s) from the Series Bible: the
    protagonist(s), else the first character listed."""
    chars = [c for c in (bible or {}).get("characters") or [] if c.get("name")]
    leads = [c for c in chars if "protagonist" in str(c.get("role") or "").lower()] or chars[:1]
    out = []
    for c in leads:
        for n in [c["name"]] + list(c.get("aliases") or []):
            n = str(n).strip()
            if len(n) >= 3 and n.lower() not in out:
                out.append(n.lower())
    return out


def _dhash(path, size=8):
    """(64-bit difference hash, mean RGB): the layout and the colour of a
    panel. Two panels are the same picture only when both are close."""
    from PIL import Image
    with Image.open(path) as im:
        rgb = im.convert("RGB")
        g = rgb.convert("L").resize((size + 1, size), Image.LANCZOS)
        px = list(g.getdata())
        mean = rgb.resize((1, 1), Image.BOX).getpixel((0, 0))
    bits = 0
    for y in range(size):
        for x in range(size):
            bits = (bits << 1) | (px[y * (size + 1) + x] > px[y * (size + 1) + x + 1])
    return bits, mean


LOOKALIKE_BITS = 10                  # of 64; at or under this = same layout
LOOKALIKE_RGB = 40                   # summed mean-colour difference


def _lookalike(a, b):
    return (bin(a[0] ^ b[0]).count("1") <= LOOKALIKE_BITS
            and sum(abs(x - y) for x, y in zip(a[1], b[1])) <= LOOKALIKE_RGB)


def rank_panels(pdir, limit=8, exclude=(), bible=None):
    """Best thumbnail candidates, grounded in panels that exist on disk.

    Scrapper's picking lessons (owner, 2026-10-04): never a bubble, blank or
    credits panel; the series lead (by the Series Bible's names) ranks up; no
    two look-alikes; `exclude` (the panels the last set used) sinks to the end
    so "Regenerate" offers the next-best instead of the same picks.
    """
    descs = _read(os.path.join(pdir, "descriptions.json"), []) or []
    if isinstance(descs, dict):
        descs = list(descs.values())
    segs = _read(os.path.join(pdir, "segments.json"), []) or []
    by_panel = {}
    for s in segs:
        by_panel.setdefault(s.get("panel_id"), s)
    leads = lead_names(bible)
    excl = set(exclude or ())
    out = []
    for d in descs:
        pid = d.get("panel_id")
        if _not_thumbnail(d):
            continue
        f = _resolve_panel(pdir, d.get("file"), pid)
        if not f:
            continue
        sc = score_panel(d, by_panel.get(pid))
        text = ((d.get("visual_description") or "") + " " + (d.get("ocr_text") or "")).lower()
        lead = next((n for n in leads if re.search(r"\b" + re.escape(n) + r"\b", text)), "")
        if lead:
            sc["lead"] = 14
            sc["subject"] += 14
            sc["total"] += 14
        out.append({"panel_id": pid, "file": f, "score": sc, "lead": lead,
                    "ticked": bool((by_panel.get(pid) or {}).get("user_included")),
                    "why": (d.get("visual_description") or "")[:150]})
    out.sort(key=lambda x: (x["panel_id"] in excl, -x["score"]["total"]))
    kept, hashes = [], []
    for c in out:
        try:
            h = _dhash(c["file"])
        except Exception:
            h = None
        if h is not None and any(_lookalike(h, k) for k in hashes):
            continue
        if h is not None:
            hashes.append(h)
        kept.append(c)
        if len(kept) >= limit:
            break
    return kept


def _read(path, default=None):
    try:
        with open(path, encoding="utf-8") as f:
            return json.load(f)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return default


# =========================================================== STYLE PACK
def series_key(meta):
    """Stable per-SERIES id. Chapters of one manhwa must collide on purpose —
    that collision is what makes the style carry forward."""
    s = (meta or {}).get("series") or ""
    k = re.sub(r"[^a-z0-9]+", "-", s.lower()).strip("-")
    return k or "unknown-series"


def _styles_dir(root):
    d = os.path.join(root, STYLES_DIR)
    os.makedirs(d, exist_ok=True)
    return d


def load_style(root, key):
    return _read(os.path.join(_styles_dir(root), "%s.json" % key), {}) or {}


def save_style(root, key, pack):
    p = os.path.join(_styles_dir(root), "%s.json" % key)
    tmp = p + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(pack, f, indent=1)
    os.replace(tmp, p)
    return pack


# Spec 07 §3 layout geometry (1280x720). The text block and the face never
# intersect; when the crop puts the face on the left, the composition mirrors.
ZONES = {"text": (28, 470, 672, 692), "face": (700, 40, 1252, 560),
         "badge": (28, 28, 232, 88), "inset": (900, 470, 1252, 692)}

COMPOSITIONS = ("cover-badge", "anchor-split", "hero-focus", "badge-stack", "diptych", "cover-frame")


def build_style_pack(pdir, meta, composition="cover-badge"):
    """Derive a series identity from the manhwa's OWN artwork.

    Proposed, not approved: until the operator approves it this is a draft, so
    a bad auto-derivation never silently becomes a series' permanent look.
    """
    # Fetch the series cover once, if we do not already have it. Best-effort:
    # a failure just means the anchor falls back to a chapter page.
    if not glob.glob(os.path.join(pdir, "pages", COVER_NAME + ".*")):
        fetch_series_cover(pdir, (meta or {}).get("url") or "")
    covers = cover_candidates(pdir)
    panels = [p["file"] for p in rank_panels(pdir, 4)]
    pal = extract_palette(covers + panels)
    return {
        "version": STYLE_VERSION,
        "series": (meta or {}).get("series") or "",
        "aliases": [],
        "anchor_images": [os.path.relpath(c, pdir) for c in covers[:1]],
        "anchor_is_real_cover": bool(covers and COVER_NAME in os.path.basename(covers[0])),
        "anchor_source_project": os.path.basename(pdir.rstrip("/")),
        "palette": {"bg": list(pal["bg"]), "accent": list(pal["accent"]),
                    "ink": list(readable_ink(pal["bg"])),
                    "swatches": pal["swatches"]},
        "typography": {"family": "bold-sans", "case": "upper",
                       "max_words": MAX_HOOK_WORDS},
        "badge": {"style": "corner-block", "label": "CH",
                  "position": "bottom-left"},
        "composition": composition if composition in COMPOSITIONS else "anchor-split",
        "text_zone": "lower-left",
        "prefers_text": True,
        "approved": False,
        "approved_at": None,
        "created_at": time.time(),
    }


def ensure_style(root, pdir, meta, force=False):
    """The consistency rule, in one function.

    An APPROVED pack is returned untouched — that is the entire point: chapter
    two must not quietly redesign itself. A draft is refreshed, and `force`
    is the explicit "give this series a new look" escape hatch.
    """
    key = series_key(meta)
    pack = load_style(root, key)
    if pack and pack.get("approved") and not force:
        pack["inherited"] = True
        return key, pack
    fresh = build_style_pack(pdir, meta,
                            composition=(pack or {}).get("composition", "cover-badge"))
    if pack and not force:
        fresh["approved"] = pack.get("approved", False)
        fresh["approved_at"] = pack.get("approved_at")
        fresh["created_at"] = pack.get("created_at", fresh["created_at"])
    fresh["inherited"] = False
    save_style(root, key, fresh)
    return key, fresh


def approve_style(root, key, pack=None, **overrides):
    pack = dict(pack or load_style(root, key) or {})
    pack.update({k: v for k, v in overrides.items() if v is not None})
    pack["approved"] = True
    pack["approved_at"] = time.time()
    return save_style(root, key, pack)


# ======================================================= HOOK TEXT
def part_from_title(title):
    """The part marker the channel already uses, e.g. '**NEW PART 10**' -> '10'.

    Serial viewers navigate by part, not chapter, so when the title carries one
    it is the more useful number to put on the cover.
    """
    m = re.search(r"\b(?:new\s+)?(?:part|pt|ep|episode)\s*([0-9]+(?:\s*-\s*[0-9]+)?)",
                  title or "", re.I)
    return re.sub(r"\s*-\s*", "-", m.group(1)) if m else ""


def hook_from_title(title, card=None):
    """A 2-5 word overlay derived from the chosen title.

    Derived rather than model-written so the thumbnail can never promise
    something the title does not, and so it needs no API call. The title is the
    verbal hook; this is the same hook compressed until it is legible at
    sidebar size.
    """
    t = re.sub(r"^\s*(\*\*[^*]*\*\*|\*[^*]*\*|\([^)]*\)|\[[^\]]*\])\s*", "", title or "")
    t = re.sub(r"[^A-Za-z0-9' ]+", " ", t)
    words = [w for w in t.split() if w]
    caps = [w for w in words if w.isupper() and len(w) > 2 and w.lower() not in _WEAK]
    strong = [w for w in words if w.lower() not in _WEAK]
    pick = caps[:3] if len(caps) >= 2 else strong[:MAX_HOOK_WORDS]
    out = []
    for w in pick:
        if len(" ".join(out + [w])) > MAX_HOOK_CHARS:
            break
        out.append(w)
    return " ".join(out).upper()[:MAX_HOOK_CHARS]


def validate_hook(text):
    """Overlay limits are enforced, not merely requested."""
    t = (text or "").strip()
    words = [w for w in t.split() if w]
    problems = []
    if len(words) > MAX_HOOK_WORDS:
        problems.append("more than %d words" % MAX_HOOK_WORDS)
    if len(t) > MAX_HOOK_CHARS:
        problems.append("longer than %d characters" % MAX_HOOK_CHARS)
    return {"ok": not problems, "problems": problems,
            "text": " ".join(words[:MAX_HOOK_WORDS])[:MAX_HOOK_CHARS].upper()}


# ========================================================= CONCEPTS
_TYPE_FOR = {"action": "action", "emotion": "emotion", "reveal": "transformation"}


def _dominant_type(panel):
    sc = panel.get("score", {})
    best = max(("action", "emotion", "reveal"), key=lambda k: sc.get(k, 0))
    return _TYPE_FOR[best] if sc.get(best, 0) > 0 else "character"


def _shift_hue(rgb, deg):
    r, g, b = [max(0, min(255, int(x))) / 255.0 for x in (list(rgb) + [0, 0, 0])[:3]]
    h, l, s_ = colorsys.rgb_to_hls(r, g, b)
    r, g, b = colorsys.hls_to_rgb((h + deg / 360.0) % 1.0, l, s_)
    return [int(r * 255), int(g * 255), int(b * 255)]


# Owner, 2026-10-05: "↻ New options doesn't give anything new". Each press
# (round) now rotates which designs are offered, picks different panels (the
# used ones are excluded) and shifts the accent colour. The cover option is
# always first and stays the same, as the owner's go-to.
DESIGN_POOL = [
    ("Series anchor + chapter hook", "anchor-split", True,
     "The series anchor keeps the upload recognisable in a feed; the chapter panel supplies what is new."),
    ("Full-bleed hook panel", "hero-focus", True, "One focal moment at full bleed reads fastest at sidebar size."),
    ("Clean picture", "clean", False, "Just the art, no text or badge."),
    ("Two moments", "diptych", True, "Two of the chapter's strongest panels side by side: setup and payoff."),
    ("Cover on the moment", "cover-frame", False,
     "The series cover as a card over a blurred moment from this chapter: recognisable and new at once."),
    ("Badge-forward variant", "badge-stack", True, "Chapter number is the loudest element — helps serial viewers find the next part."),
]


def build_concepts(pdir, meta, style, title="", n=4, exclude=(), bible=None, round_=0):
    """3-5 chapter concepts, all built on the SAME approved series style.

    Only three things vary between chapters by design: the focal panel, the
    chapter badge, and the hook text. Everything else — palette, composition,
    badge style, typography — comes from the style pack, which is what makes a
    playlist of these look like one series.
    """
    panels = rank_panels(pdir, 8, exclude=exclude, bible=bible)
    chapter = str((meta or {}).get("chapter") or "").strip()
    hook = hook_from_title(title) if title else ""
    pal = (style or {}).get("palette", {})
    comp = (style or {}).get("composition", "anchor-split")
    inherited = bool((style or {}).get("approved"))
    anchors = (style or {}).get("anchor_images") or []
    out = []
    if not panels:
        return out

    # Concept 1 — always the series cover with the chapter number (the owner's
    # go-to); then three designs from the pool, rotated each round.
    part = part_from_title(title)
    plans = []
    # QA (owner, 2026-10-06): the cover design is only offered when THIS
    # chapter has the series' real cover file; the anchor of a series style
    # can point at another chapter's page or a story panel.
    real_cover = cover_file(pdir)
    if real_cover:
        anchors = [os.path.relpath(real_cover, pdir)] + [a for a in anchors if a != os.path.relpath(real_cover, pdir)]
    if real_cover:
        plans.append(("Cover art + chapter number", "cover-badge", panels[0], False,
                      "The series cover with just the chapter number on it \u2014 "
                      "the same image every chapter, so a playlist reads as one "
                      "series and only the number changes."))
    k = len(DESIGN_POOL)
    picks = [DESIGN_POOL[(round_ * 3 + i) % k] for i in range(k)]
    if not real_cover:
        picks = [p for p in picks if p[1] != "cover-frame"]       # needs the real cover too
    if not anchors:
        picks = [p for p in picks if p[1] not in ("anchor-split", "cover-frame")]
    want = 3 if real_cover else 4
    for di, (name, composition, use_text, why) in enumerate(picks[:want]):
        panel = panels[min(di, len(panels) - 1)]
        if composition == "clean":
            panel = next((p for p in panels if p.get("lead")), panel)
            if panel.get("lead"):
                why = why[:-1] + " \u2014 the series lead (%s)." % panel["lead"].title()
        plans.append((name, composition, panel, use_text and bool(hook), why))
    pal_r = dict(pal)
    if round_ and pal.get("accent"):
        pal_r["accent"] = _shift_hue(pal["accent"], (round_ * 67) % 360)

    for i, (name, composition, panel, use_text, why) in enumerate(plans[:max(4, min(n, 5))]):
        out.append({
            "id": "c%d" % i,
            "name": name,
            "type": _dominant_type(panel),
            "composition": composition,
            "focal_panel": panel["panel_id"],
            "focal_file": panel["file"],
            "focal_why": panel["why"],
            "panel_score": panel["score"],
            # cover-badge needs the anchor too — it IS the anchor, full-bleed.
            "anchor_image": (anchors[0] if anchors and composition in
                             ("anchor-split", "cover-badge", "cover-frame") else None),
            "second_file": (panels[1]["file"] if composition == "diptych" and len(panels) > 1 else None),
            "chapter": chapter,
            "part": part,
            "badge": ((style or {}).get("badge") or {}).get("label", "CH"),
            "overlay_text": hook if use_text else "",
            "text_zone": (style or {}).get("text_zone", "lower-left"),
            "palette": pal if composition == "cover-badge" else pal_r,
            "round": round_,
            "follows_series_style": inherited and composition == comp,
            "style_relation": ("inherits the approved series style"
                               if inherited and composition == comp
                               else "variation on the series style"),
            "title_alignment": _title_alignment(title, panel, hook),
            "why": why,
        })
    return out


def _title_alignment(title, panel, hook):
    """Do the verbal hook and the visual hook tell the same story?"""
    if not title:
        return {"score": 0, "note": "no title chosen yet — concepts are built "
                                    "from project truth alone"}
    t = (title or "").lower()
    words = {w for w in re.findall(r"[a-z]{4,}", t) if w not in _WEAK}
    blurb = (panel.get("why") or "").lower()
    overlap = sum(1 for w in words if w in blurb)
    s = min(20, overlap * 5) + (8 if hook and hook.lower() in t.lower() else 0)
    return {"score": s,
            "note": ("the focal panel shows what the title promises"
                     if s >= 10 else "loose link between title and focal panel")}


def score_concept(c, style, title=""):
    """Computed recommendation — never taken on trust.

    Weighted towards what survives being shrunk: focal clarity and readability
    matter more than how interesting the moment is in full size.
    """
    ps = c.get("panel_score", {})
    focal = min(28, int(ps.get("shape", 0)) + int(ps.get("subject", 0)))
    drama = min(22, int(ps.get("action", 0)) + int(ps.get("emotion", 0)) +
                int(ps.get("reveal", 0)))
    consistency = 20 if c.get("follows_series_style") else (
        10 if (style or {}).get("approved") else 14)
    align = min(20, int((c.get("title_alignment") or {}).get("score", 0)))
    # Readability: text is a benefit only while it stays short.
    txt = c.get("overlay_text") or ""
    readability = 10
    if txt:
        v = validate_hook(txt)
        readability = 10 if v["ok"] else 2
    clutter = 0
    if c.get("composition") == "anchor-split" and len(txt.split()) > 4:
        clutter = -6            # anchor + panel + long text is three ideas
    total = max(0, focal + drama + consistency + align + readability + clutter)
    return {"total": int(total), "focal": focal, "drama": drama,
            "consistency": consistency, "title_alignment": align,
            "readability": readability, "clutter": clutter}


def rank_concepts(concepts, style, title=""):
    for c in concepts:
        c["score"] = score_concept(c, style, title)
        c["recommended"] = False
    if concepts:
        concepts.sort(key=lambda c: (c.get("composition") != "cover-badge", -c["score"]["total"]))
        concepts[0]["recommended"] = True       # the cover is the owner's go-to
    return concepts


# ========================================================= COMPOSITION
# Spec 07 §3 / A2: CROP TO FILL, never pillarbox. The crop is a source window
# with the box's aspect, anchored so the face lands at the face-zone centre.
FACE_TARGET = ((ZONES["face"][0] + ZONES["face"][2]) / 2.0 / W,      # 0.7625
               (ZONES["face"][1] + ZONES["face"][3]) / 2.0 / H)      # 0.4167
FACE_TARGET_H = 0.34        # zoom so the face is ~a third of the frame tall
MAX_UPSCALE = 2.5           # never zoom past 2.5x the source pixels
NO_FACE_TOP = 0.12          # no face: the band starts at 12% of the height
BACKDROP_UPSCALE = 1.9      # 3B step 4: above this upscale, sharp-on-blur
SHARP_SHARE = 0.62          # ...with the sharp art on the right 62%
SHARP_MIN_SHARE = 0.60      # never less than 60% of the frame
FEATHER_PX = 40


def crop_window(sw, sh, box_w, box_h, face=None, target=FACE_TARGET, zoom=True):
    """Source rectangle (x0, y0, x1, y1) with the box's aspect that FILLS it.

    face: normalised [x0, y0, x1, y1] on the source. With a face the window is
    zoomed (up to MAX_UPSCALE) toward FACE_TARGET_H and positioned so the face
    centre lands at `target` (fractions of the box), then clamped to the image.
    Without one: centred across, the 12%-height line at the face height (3B step 2)."""
    ar = box_w / float(box_h)
    cw = min(float(sw), sh * ar)
    ch = cw / ar
    if face:
        fx0, fy0, fx1, fy1 = face[0] * sw, face[1] * sh, face[2] * sw, face[3] * sh
        if zoom:
            want = (fy1 - fy0) / FACE_TARGET_H
            floor_h = box_h / MAX_UPSCALE
            nh = min(ch, max(want, floor_h, (fx1 - fx0) / ar / 0.40))
            ch, cw = nh, nh * ar
        cx = (fx0 + fx1) / 2.0 - target[0] * cw
        cy = (fy0 + fy1) / 2.0 - target[1] * ch
    else:
        # "bias to y = 12% of the cover height, where the face usually sits":
        # that line goes where a face would go (the face-zone height).
        cx = (sw - cw) / 2.0
        cy = sh * NO_FACE_TOP - target[1] * ch
    cx = max(0.0, min(cx, sw - cw))
    cy = max(0.0, min(cy, sh - ch))
    return (cx, cy, cx + cw, cy + ch)


def face_in_box(face, window, sw, sh, box_w, box_h):
    """The face box in OUTPUT pixels for a given source window."""
    x0, y0, x1, y1 = window
    kx, ky = box_w / (x1 - x0), box_h / (y1 - y0)
    return (int((face[0] * sw - x0) * kx), int((face[1] * sh - y0) * ky),
            int((face[2] * sw - x0) * kx), int((face[3] * sh - y0) * ky))


def mirror_box(b):
    return [round(1 - b[2], 4), b[1], round(1 - b[0], 4), b[3]] if b else b


def _cover_crop(im, box_w, box_h, face=None, target=FACE_TARGET, zoom=True):
    """Fill the box from a face-anchored window — never letterbox/pillarbox."""
    from PIL import Image
    sw, sh = im.size
    if not sw or not sh:
        return im.resize((box_w, box_h))
    win = crop_window(sw, sh, box_w, box_h, face, target, zoom)
    return im.resize((box_w, box_h), Image.LANCZOS, box=tuple(win))


def upscale_of(im_size, box_w, box_h, face=None, target=FACE_TARGET):
    win = crop_window(im_size[0], im_size[1], box_w, box_h, face, target)
    return box_h / max(1e-6, win[3] - win[1])


# Aggregator watermarks ("ASURASCANS.COM" badge, site URLs) are stamped on the
# top or bottom edge of the scraped series cover. The owner chose (2026-10-05)
# to keep the cover option and trim them off: the edge bands are cut before the
# cover is placed, so neither the picture nor its blurred backdrop shows them.
# Tunable per deploy; a watermark placed elsewhere on the cover is not caught.
COVER_TRIM_TOP = float(os.environ.get("THUMB_COVER_TRIM_TOP", "0.07"))
COVER_TRIM_BOTTOM = float(os.environ.get("THUMB_COVER_TRIM_BOTTOM", "0.05"))
TITLE_BAND_MAX = 0.45       # the cover's own logo never takes more than this


WIDE_BLOCK = 0.5            # a text block this wide is a band (logo); narrower is a mark


def title_band_from_blocks(blocks):
    """Spec A5: the cover's own typography (title logo, credits, watermark) from
    the text-block boxes the face pass returns for the cover (face_boxes.py
    `text_blocks`). Returns (top_frac, bottom_frac, marks):

      * a WIDE block (>= half the width) in the bottom 45% / top third becomes
        an edge trim, from that edge to the block's far side;
      * a NARROW block (a corner watermark, a credit tag) is returned in
        `marks` to be inpainted from its surroundings — trimming the whole row
        band for a corner badge cut the MC's head off the ch.358 cover.

    Measured on ch.358's cover: a pixel edge-density detector could not tell
    the brush-lettered logo from the painted art, so detection is the model's
    box, never a guess."""
    top, bottom, marks = 0.0, 0.0, []
    for b in blocks or []:
        try:
            x0, y0, x1, y1 = [float(v) for v in b[:4]]
        except Exception:
            continue
        if x1 - x0 < WIDE_BLOCK:
            marks.append([x0, y0, x1, y1])
        elif y0 >= 1 - TITLE_BAND_MAX:
            bottom = max(bottom, min(TITLE_BAND_MAX, 1 - y0 + 0.01))
        elif y1 <= 0.34:
            top = max(top, min(0.34, y1 + 0.01))
        else:
            marks.append([x0, y0, x1, y1])
    return round(top, 3), round(bottom, 3), marks


def cover_trim_for(im, style=None, text_blocks=None):
    """Per-series trim: the style pack's `cover_trim` wins (owner override),
    else the detected title band + marks, else the watermark defaults.
    Returns (top_frac, bottom_frac, source, marks)."""
    ct = (style or {}).get("cover_trim") or {}
    if ct.get("top_pct") is not None and ct.get("bottom_pct") is not None:
        return float(ct["top_pct"]) / 100.0, float(ct["bottom_pct"]) / 100.0, "style pack", []
    if text_blocks:
        t, b, marks = title_band_from_blocks(text_blocks)
        return t, b, "detected", marks
    return COVER_TRIM_TOP, COVER_TRIM_BOTTOM, "default (no logo box)", []


COVER_FACE_MIN_AREA = 0.01  # a cover face smaller than 1% of the cover is a crowd face


def pick_face(rec, min_area=0.0):
    """The face to anchor on from a face_boxes record: the largest MC face,
    else the largest face of at least `min_area` (fraction of the image)."""
    faces = [f for f in (rec or {}).get("faces") or [] if f.get("box")]
    area = lambda f: (f["box"][2] - f["box"][0]) * (f["box"][3] - f["box"][1])
    mc = [f for f in faces if f.get("is_mc")]
    for pool in (mc, [f for f in faces if area(f) >= min_area]):
        if pool:
            return max(pool, key=area)
    return None


def _trim_watermark_bands(im, trim=None):
    w, h = im.size
    tt, tb = (trim or (COVER_TRIM_TOP, COVER_TRIM_BOTTOM))[:2]
    top, bottom = int(h * tt), int(h * tb)
    if h - top - bottom < h * 0.45:
        return im
    return im.crop((0, top, w, h - bottom))


def _backdrop_fit(im, box_w, box_h, face=None):
    """Spec 3B step 4 — the ONLY permitted background fill: a blurred copy of
    the art fills the frame and the SHARP face-anchored crop covers the right
    62% (never under 60%), feathered 40 px into the blur."""
    from PIL import Image, ImageEnhance, ImageFilter
    back = _cover_crop(im, box_w, box_h, face).filter(ImageFilter.GaussianBlur(max(8, box_w // 40)))
    back = ImageEnhance.Brightness(back).enhance(0.55)
    sw_ = max(int(box_w * SHARP_MIN_SHARE), int(box_w * SHARP_SHARE))
    # inside the sharp area the face still sits at the face-zone centre
    tx = (FACE_TARGET[0] * box_w - (box_w - sw_)) / float(sw_)
    sharp = _cover_crop(im, sw_, box_h, face, target=(tx, FACE_TARGET[1]))
    mask = Image.new("L", (sw_, box_h), 255)
    px = mask.load()
    for x in range(min(FEATHER_PX, sw_)):
        v = int(255 * x / float(FEATHER_PX))
        for y in range(box_h):
            px[x, y] = v
    back.paste(sharp, (box_w - sw_, 0), mask)
    return back


# ---- A3: the arrow (spec 07 §4.2)
ARROW = {"shaft": 16, "tip_shaft": 12, "head": 56, "outline": 6,
         "min_len": 200, "max_len": 340, "gap": (18, 30)}


def _arrow(img, start_xy, end_xy, fill=HOOK_FILL, outline=(0, 0, 0)):
    """A straight arrow, tip at end_xy: 16 px shaft tapering toward a 56x56
    head, `fill` with a 6 px `outline` drawn OUTSIDE the shape (the fill keeps
    its full width). Anti-aliased by drawing at 3x."""
    import math
    from PIL import Image, ImageDraw, ImageFilter
    sx, sy = start_xy
    ex, ey = end_xy
    L = math.hypot(ex - sx, ey - sy)
    if L < 1:
        return None
    ux, uy = (ex - sx) / L, (ey - sy) / L
    nx, ny = -uy, ux
    hd = ARROW["head"]
    bx, by = ex - ux * hd, ey - uy * hd                # head base centre
    s0, s1 = ARROW["shaft"] / 2.0, ARROW["tip_shaft"] / 2.0
    poly = [(sx + nx * s0, sy + ny * s0), (bx + nx * s1, by + ny * s1),
            (bx + nx * hd / 2, by + ny * hd / 2), (ex, ey),
            (bx - nx * hd / 2, by - ny * hd / 2), (bx - nx * s1, by - ny * s1),
            (sx - nx * s0, sy - ny * s0)]
    S = 3
    o = ARROW["outline"]
    x0 = int(min(p[0] for p in poly)) - o - 4
    y0 = int(min(p[1] for p in poly)) - o - 4
    x1 = int(max(p[0] for p in poly)) + o + 4
    y1 = int(max(p[1] for p in poly)) + o + 4
    big = Image.new("L", ((x1 - x0) * S, (y1 - y0) * S), 0)
    ImageDraw.Draw(big).polygon([((x - x0) * S, (y - y0) * S) for x, y in poly], fill=255)
    m_fill = big.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    m_line = big.filter(ImageFilter.MaxFilter(2 * o * S + 1))     # outline = dilation
    m_line = m_line.resize((x1 - x0, y1 - y0), Image.LANCZOS)
    img.paste(Image.new("RGBA", m_line.size, tuple(outline) + (255,)), (x0, y0), m_line)
    img.paste(Image.new("RGBA", m_fill.size, tuple(fill) + (255,)), (x0, y0), m_fill)
    return (x0, y0, x1, y1)


def _ray_box_exit(px, py, ux, uy, box):
    """Distance along (ux,uy) from (px,py) to the first edge of `box`."""
    bx0, by0, bx1, by1 = box
    ts = []
    for t in (((bx0 - px) / ux) if ux else None, ((bx1 - px) / ux) if ux else None,
              ((by0 - py) / uy) if uy else None, ((by1 - py) / uy) if uy else None):
        if t is not None and t > 0:
            x, y = px + ux * t, py + uy * t
            if bx0 - 0.5 <= x <= bx1 + 0.5 and by0 - 0.5 <= y <= by1 + 0.5:
                ts.append(t)
    return min(ts) if ts else None


def arrow_path(text_box, face_box, canvas=(W, H), gap=24):
    """Start on the outer edge of the text block (its face-side top corner),
    end `gap` px short of the face box, length clamped to 200-340 px."""
    import math
    if not face_box:
        return None
    fx = (face_box[0] + face_box[2]) / 2.0
    fy = (face_box[1] + face_box[3]) / 2.0
    if text_box:
        tx0, ty0, tx1, ty1 = text_box
        sx = tx1 - 40 if fx > (tx0 + tx1) / 2.0 else tx0 + 40
        sy = ty0 - 14
    else:
        sx, sy = (canvas[0] * 0.30, canvas[1] * 0.72)
    L = math.hypot(fx - sx, fy - sy)
    if L < 1:
        return None
    ux, uy = (fx - sx) / L, (fy - sy) / L
    # distance from start to the face edge along the ray
    t_in = _ray_box_exit(sx, sy, ux, uy, face_box)
    if t_in is None:
        return None
    end_t = t_in - gap
    length = max(ARROW["min_len"], min(ARROW["max_len"], end_t))
    st = end_t - length                                 # move the start, never the tip
    return ((sx + ux * st, sy + uy * st), (sx + ux * end_t, sy + uy * end_t))


# ---- A4: bubble suppression (spec 07 §5, composite stage)
def inpaint_rect(img, rect, margin=28, passes=14):
    """Fill `rect` from its own surroundings: seed with the ring's median
    colour, diffuse the ring in with repeated blurs (only inside the rect),
    finish with a median filter. Nothing new is drawn — it is the panel's own
    pixels spread over the bubble."""
    from PIL import Image, ImageFilter, ImageStat
    x0, y0, x1, y1 = [int(v) for v in rect]
    W_, H_ = img.size
    x0, y0, x1, y1 = max(0, x0), max(0, y0), min(W_, x1), min(H_, y1)
    if x1 - x0 < 2 or y1 - y0 < 2:
        return
    ex0, ey0 = max(0, x0 - margin), max(0, y0 - margin)
    ex1, ey1 = min(W_, x1 + margin), min(H_, y1 + margin)
    reg = img.crop((ex0, ey0, ex1, ey1)).convert("RGB")
    inner = Image.new("L", reg.size, 0)
    inner.paste(255, (x0 - ex0, y0 - ey0, x1 - ex0, y1 - ey0))
    ring = Image.eval(inner, lambda v: 255 - v)
    med = tuple(int(v) for v in ImageStat.Stat(reg, ring).median)
    reg.paste(med, mask=inner)
    for _ in range(passes):
        reg.paste(reg.filter(ImageFilter.GaussianBlur(10)), mask=inner)
    reg.paste(reg.filter(ImageFilter.MedianFilter(5)), mask=inner)
    img.paste(reg.convert(img.mode), (ex0, ey0), inner)


def _intersects(a, b):
    return bool(a and b and a[0] < b[2] and b[0] < a[2] and a[1] < b[3] and b[1] < a[3])


def _scrim(img, box, rgba=(0, 0, 0, 170), horizontal=False):
    """A gradient wash so overlay text has something to sit on. Without it,
    light artwork and white text collide and neither is readable."""
    from PIL import Image
    x0, y0, x1, y1 = box
    w, h = max(1, x1 - x0), max(1, y1 - y0)
    grad = Image.new("L", (w, h))
    px = grad.load()
    for i in range(w if horizontal else h):
        v = int(rgba[3] * (1 - i / float(w if horizontal else h)))
        if horizontal:
            for y in range(h):
                px[i, y] = v
        else:
            for x in range(w):
                px[x, h - 1 - i] = v
    layer = Image.new("RGBA", (w, h), rgba[:3] + (0,))
    layer.putalpha(grad)
    img.alpha_composite(layer, (x0, y0))


def _fit_text(draw, text, max_w, start=96, floor=42):
    """Shrink, then WRAP — never overflow.

    Shrinking alone bottoms out at the floor size and then silently spills past
    the zone; on a split composition that put the hook across the seam and over
    the chapter artwork. Returns (font, lines) so the caller draws real lines.
    """
    words = text.split()
    size = start
    while size >= floor:
        f = _font(size)
        if draw.textlength(text, font=f) <= max_w:
            return f, [text]
        if len(words) > 1:
            # try two balanced lines at this size before shrinking further
            for cut in range(len(words) - 1, 0, -1):
                a, b = " ".join(words[:cut]), " ".join(words[cut:])
                if (draw.textlength(a, font=f) <= max_w
                        and draw.textlength(b, font=f) <= max_w):
                    return f, [a, b]
        size -= 4
    f = _font(floor)
    return f, [text]


def render_concept(pdir, concept, style, out_path, width=W):
    """Compose the thumbnail from the project's OWN panel files.

    Everything placed here already exists in the project: the panel crops the
    renderer put in the video, and the source page used as the series anchor.
    Nothing is drawn or synthesised — this is layout, which is why the result
    is reproducible and always matches the chapter.
    """
    from PIL import Image, ImageDraw
    print(json.dumps({"service": "thumbnail_studio", "event": "render", "font": font_path(),
                      "composition": concept.get("composition"), "out": os.path.basename(out_path)}), flush=True)
    width = max(MIN_W, int(width))
    height = int(round(width * 9 / 16.0))
    pal = (concept.get("palette") or (style or {}).get("palette") or {})
    bg = tuple((pal.get("bg") or [14, 16, 22])[:3])
    accent = tuple((pal.get("accent") or [0, 213, 255])[:3])
    ink = tuple((pal.get("ink") or list(readable_ink(bg)))[:3])

    img = Image.new("RGBA", (width, height), bg + (255,))
    comp = concept.get("composition", "anchor-split")
    focal = concept.get("focal_file")
    anchor = concept.get("anchor_image")
    if anchor and not os.path.isabs(anchor):
        anchor = os.path.join(pdir, anchor)

    def place(path, box, face=None, target=FACE_TARGET, backdrop_ok=False):
        """Crop-to-fill `box` from `path` (spec A2: never pillarbox). Returns
        the face box in canvas pixels, True when placed without a face, or
        False when nothing could be placed."""
        if not (path and os.path.exists(path)):
            return False
        try:
            with Image.open(path) as src:
                im = src.convert("RGBA")
                if anchor and os.path.abspath(path) == os.path.abspath(anchor):
                    trim = cover_trim_for(im, style, concept.get("cover_text_blocks"))
                    concept["_cover_trim"] = {"top_pct": round(trim[0] * 100, 1),
                                              "bottom_pct": round(trim[1] * 100, 1), "source": trim[2],
                                              "marks_inpainted": len(trim[3])}
                    for m in trim[3]:
                        inpaint_rect(im, (m[0] * im.width - 4, m[1] * im.height - 4,
                                          m[2] * im.width + 4, m[3] * im.height + 4))
                    h0 = im.height
                    im = _trim_watermark_bands(im, trim)
                    if face and im.height != h0:   # face box was measured on the untrimmed cover
                        top = int(h0 * trim[0])
                        face = [face[0], (face[1] * h0 - top) / im.height, face[2],
                                (face[3] * h0 - top) / im.height]
                        if face[1] < 0 or face[3] > 1:
                            face = None
                bw, bh = box[2] - box[0], box[3] - box[1]
                if backdrop_ok and upscale_of(im.size, bw, bh, face, target) > BACKDROP_UPSCALE:
                    img.alpha_composite(_backdrop_fit(im, bw, bh, face), (box[0], box[1]))
                    concept["_backdrop"] = True
                    sw_ = int(bw * SHARP_SHARE)
                    tx = (FACE_TARGET[0] * bw - (bw - sw_)) / float(sw_)
                    win = crop_window(im.width, im.height, sw_, bh, face, (tx, FACE_TARGET[1]))
                    fb = face_in_box(face, win, im.width, im.height, sw_, bh) if face else None
                    return (True if not fb else
                            (fb[0] + box[0] + bw - sw_, fb[1] + box[1], fb[2] + box[0] + bw - sw_, fb[3] + box[1]))
                win = crop_window(im.width, im.height, bw, bh, face, target)
                up = bh / max(1e-6, win[3] - win[1])
                tile = im.resize((bw, bh), Image.LANCZOS, box=tuple(win))
                if up > 1.3:                       # 3A step 3: soft crop -> unsharp mask
                    from PIL import ImageFilter
                    tile = tile.filter(ImageFilter.UnsharpMask(radius=2, percent=80, threshold=2))
                img.alpha_composite(tile, (box[0], box[1]))
                if not face:
                    return True
                fb = face_in_box(face, win, im.width, im.height, bw, bh)
                return (fb[0] + box[0], fb[1] + box[1], fb[2] + box[0], fb[3] + box[1])
        except Exception as e:
            print(json.dumps({"service": "thumbnail_studio", "event": "place_failed",
                              "file": os.path.basename(str(path)), "error": str(e)[:200]}), flush=True)
            return False

    if comp == "cover-badge":
        # The cover fills the frame; the number is the only thing added. QA:
        # never fall back to a story panel under the name "Cover art" (the
        # 2026-10-06 bug) — no real cover, no cover design.
        if not (anchor and os.path.exists(anchor) and COVER_NAME in os.path.basename(anchor)
                and place(anchor, (0, 0, width, height), concept.get("cover_face_box"),
                          backdrop_ok=True)):
            raise ValueError("no series cover for this chapter — the Cover art design can't be made")
    elif comp == "anchor-split" and anchor and os.path.exists(anchor):
        split = int(width * 0.42)
        place(anchor, (0, 0, split, height))
        if not place(focal, (split, 0, width, height)):
            place(anchor, (split, 0, width, height))
        # a hard accent seam makes the two halves read as one deliberate design
        ImageDraw.Draw(img).rectangle([split - 4, 0, split + 1, height],
                                      fill=accent + (255,))
        _scrim(img, (0, int(height * 0.52), split, height))
    elif comp == "clean":
        place(focal, (0, 0, width, height))
    elif comp == "diptych":
        second = concept.get("second_file")
        half = width // 2
        place(focal, (0, 0, half, height))
        if not place(second, (half, 0, width, height)):
            place(focal, (half, 0, width, height))
        ImageDraw.Draw(img).rectangle([half - 3, 0, half + 3, height], fill=accent + (255,))
        _scrim(img, (0, int(height * 0.55), width, height), (0, 0, 0, 185))
    elif comp == "cover-frame" and anchor and os.path.exists(anchor):
        from PIL import ImageFilter, ImageEnhance
        place(focal, (0, 0, width, height))
        img = ImageEnhance.Brightness(img.filter(ImageFilter.GaussianBlur(max(6, width // 60)))).enhance(0.55)
        try:
            with Image.open(anchor) as src:
                cv = _trim_watermark_bands(src.convert("RGBA"))
            ch_ = int(height * 0.86)
            cw_ = int(cv.width * ch_ / max(1, cv.height))
            cv = cv.resize((cw_, ch_), Image.LANCZOS)
            x0 = width - cw_ - int(width * 0.06)
            y0 = (height - ch_) // 2
            ImageDraw.Draw(img).rectangle([x0 - 6, y0 - 6, x0 + cw_ + 6, y0 + ch_ + 6], fill=accent + (255,))
            img.alpha_composite(cv, (x0, y0))
        except Exception:
            pass
        _scrim(img, (0, int(height * 0.5), int(width * 0.6), height), (0, 0, 0, 160))
    elif comp == "badge-stack":
        place(focal, (0, 0, width, height))
        _scrim(img, (0, int(height * 0.45), width, height), (0, 0, 0, 190))
    else:                                    # hero-focus
        place(focal, (0, 0, width, height))
        _scrim(img, (0, int(height * 0.5), width, height), (0, 0, 0, 185))

    draw = ImageDraw.Draw(img)
    pad = int(width * 0.035)

    # Chapter badge — the one element that must change every chapter.
    part = str(concept.get("part") or "").strip()
    ch = part or str(concept.get("chapter") or "").strip()
    if ch and comp != "clean":
        label = "%s %s" % ("PART" if part else (concept.get("badge") or "CH"), ch)
        # Owner, 2026-10-05: the badge covered the cover art — smaller, top-left.
        big = comp == "badge-stack"
        bf = _font(int(height * (0.12 if big else 0.07)))
        tw = draw.textlength(label, font=bf)
        bh = int(height * (0.16 if big else 0.095))
        bw = int(tw + pad * (1.6 if big else 1.2))
        bx, by = pad, (height - bh - pad if big else pad)
        draw.rectangle([bx, by, bx + bw, by + bh], fill=accent + (255,))
        draw.text((bx + bw / 2, by + bh / 2), label, font=bf,
                  fill=readable_ink(accent), anchor="mm")

    # Hook text — short by construction, and clamped again here.
    txt = validate_hook(concept.get("overlay_text") or "")["text"]
    text_box = None
    if txt:
        # Spec 07 §1/§4.1: word-count cap heights, 84 px floor, words dropped
        # rather than shrunk; text zone x 28..672, y 470..692 (on 1280x720).
        k = width / float(W)
        zx0, zy0, zx1, zy1 = (int(v * k) for v in (ZONES["text"][0], ZONES["text"][1], ZONES["text"][2], ZONES["text"][3]))
        if comp == "anchor-split":
            zx1 = min(zx1, int(width * 0.42) - int(28 * k))
        lay = hook_layout(txt, zx1 - zx0, zy1 - zy0)
        if lay:
            text_box = draw_hook(img, lay, zx0, zy1)
            concept["_hook_layout"] = {"lines": lay["lines"], "cap": lay["cap"], "stroke": lay["stroke"],
                                       "dropped": lay["dropped"]}

    os.makedirs(os.path.dirname(out_path) or ".", exist_ok=True)
    final = img.convert("RGB")
    tmp = out_path + ".tmp"
    if out_path.lower().endswith(".png"):
        final.save(tmp, "PNG", optimize=True)
    else:
        final.save(tmp, "JPEG", quality=JPEG_Q, optimize=True, progressive=True)
    os.replace(tmp, out_path)
    return {"path": out_path, "width": width, "height": height,
            "bytes": os.path.getsize(out_path),
            "composition": comp, "has_text": bool(txt), "chapter": ch}


# ========================================================= PERSISTENCE
def load_concepts(pdir):
    return _read(os.path.join(pdir, CONCEPTS_NAME), {}) or {}


def save_concepts(pdir, data):
    tmp = os.path.join(pdir, CONCEPTS_NAME + ".tmp")
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=1)
    os.replace(tmp, os.path.join(pdir, CONCEPTS_NAME))


def get_concepts(pdir, name, current_signature=None):
    """Staleness derived at read time, matching how review and SEO already
    work — a stored flag would go out of date the moment the cut changes."""
    rec = load_concepts(pdir).get(os.path.basename(name or "")) or {}
    if rec and current_signature:
        rec = dict(rec)
        rec["stale"] = bool(rec.get("cut_signature")
                            and rec["cut_signature"] != current_signature)
    return rec


def put_concepts(pdir, name, rec):
    data = load_concepts(pdir)
    data[os.path.basename(name or "")] = rec
    save_concepts(pdir, data)
    return rec


# ======================================================= SERIES COVER ART
# The ingest scraper deliberately SKIPS "/covers/" and "thumbnail" images — it
# only wants chapter pages — so a project has no real cover art on disk. Page 1
# of a chapter is a story page, not a cover, which is why using it as the
# series anchor looked arbitrary. This fetches the actual cover once per series
# from the same source the chapter came from.
COVER_NAME = "_cover"


def series_page_url(chapter_url):
    """The series page that a chapter URL belongs to."""
    u = (chapter_url or "").strip()
    if not u:
        return ""
    u = re.sub(r"/chapter[s]?/[^/]*/?$", "/", u, flags=re.I)
    return u if u.endswith("/") else u + "/"


def _pick_cover_url(html_text, base):
    """The series cover from a series page.

    Prefers og:image — the site's own declaration of the canonical artwork for
    that series — and falls back to an image whose URL says it is a cover.
    """
    import urllib.parse
    m = re.search(r'<meta[^>]+property=["\']og:image["\'][^>]+content=["\']([^"\']+)',
                  html_text, re.I)
    if not m:
        m = re.search(r'<meta[^>]+content=["\']([^"\']+)["\'][^>]+property=["\']og:image',
                      html_text, re.I)
    if m:
        return urllib.parse.urljoin(base, m.group(1))
    for pat in (r'src=["\']([^"\']*(?:cover|thumbnail)[^"\']*\.(?:jpe?g|png|webp))',
                r'["\'](https?://[^"\']*/covers?/[^"\']+\.(?:jpe?g|png|webp))'):
        m2 = re.search(pat, html_text, re.I)
        if m2:
            return urllib.parse.urljoin(base, m2.group(1))
    return ""


def fetch_series_cover(pdir, chapter_url, timeout=30, _open=None):
    """Download the series cover into the project. Returns its path or "".

    Failure is never fatal: a missing cover simply means the copilot falls back
    to a chapter page, with the style pack recording which it used.
    """
    import ssl
    import urllib.request
    page = series_page_url(chapter_url)
    if not page:
        return ""
    ua = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
          "(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36")
    opener = _open or (lambda u: urllib.request.urlopen(
        urllib.request.Request(u, headers={"User-Agent": ua}),
        timeout=timeout, context=ssl._create_unverified_context()))
    try:
        with opener(page) as r:
            html_text = r.read().decode("utf-8", "replace")
    except Exception:
        return ""
    url = _pick_cover_url(html_text, page)
    if not url:
        return ""
    ext = os.path.splitext(url.split("?")[0])[1].lower()
    if ext not in (".jpg", ".jpeg", ".png", ".webp"):
        ext = ".jpg"
    out = os.path.join(pdir, "pages", COVER_NAME + ext)
    try:
        os.makedirs(os.path.dirname(out), exist_ok=True)
        with opener(url) as r:
            data = r.read()
        if len(data) < 2048:
            return ""
        from PIL import Image
        import io as _io
        Image.open(_io.BytesIO(data)).verify()      # refuse anything unreadable
        with open(out, "wb") as f:
            f.write(data)
        return out
    except Exception:
        return ""
