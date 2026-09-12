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
MAX_HOOK_WORDS = 5
MAX_HOOK_CHARS = 28

# Words that make a weak overlay — they carry no hook on their own.
_WEAK = set("""a an the and or but of to in on at by for with from as is are was
were be this that his her their its it he she they you your my our who what
when where how chapter part recap manhwa""".split())


# --------------------------------------------------------------- typography
def _font(size, bold=True):
    """A real bold face on both the container and a dev Mac.

    The image ships fonts-dejavu-core; macOS has neither DejaVu nor the same
    paths. Pillow's default bitmap font cannot scale, so falling back to it
    silently would render unreadable text — worth failing loudly instead.
    """
    from PIL import ImageFont
    cands = [
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
        "/System/Library/Fonts/Supplemental/Impact.ttf",
        "/System/Library/Fonts/Supplemental/Arial.ttf",
        "/Library/Fonts/Arial Bold.ttf",
    ]
    if not bold:
        cands.insert(0, "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf")
    for p in cands:
        if os.path.exists(p):
            try:
                return ImageFont.truetype(p, size)
            except Exception:
                continue
    return ImageFont.load_default()


def font_available():
    from PIL import ImageFont
    return not isinstance(_font(40), ImageFont.ImageFont)


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
def cover_candidates(pdir):
    """The series anchor: the source pages, earliest first.

    Page 1 of a scanlated chapter is the title/cover page, which is why it is
    preferred — it is the most recognisable single image a series has.
    """
    pages = sorted(glob.glob(os.path.join(pdir, "pages", "*.webp")) +
                   glob.glob(os.path.join(pdir, "pages", "*.jpg")) +
                   glob.glob(os.path.join(pdir, "pages", "*.png")))
    return pages[:3]


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


def rank_panels(pdir, limit=8):
    """Best thumbnail candidates, grounded in panels that exist on disk."""
    descs = _read(os.path.join(pdir, "descriptions.json"), []) or []
    if isinstance(descs, dict):
        descs = list(descs.values())
    segs = _read(os.path.join(pdir, "segments.json"), []) or []
    by_panel = {}
    for s in segs:
        by_panel.setdefault(s.get("panel_id"), s)
    out = []
    for d in descs:
        pid = d.get("panel_id")
        f = _resolve_panel(pdir, d.get("file"), pid)
        if not f:
            continue
        sc = score_panel(d, by_panel.get(pid))
        out.append({"panel_id": pid, "file": f, "score": sc,
                    "ticked": bool((by_panel.get(pid) or {}).get("user_included")),
                    "why": (d.get("visual_description") or "")[:150]})
    out.sort(key=lambda x: -x["score"]["total"])
    return out[:limit]


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


COMPOSITIONS = ("anchor-split", "hero-focus", "badge-stack")


def build_style_pack(pdir, meta, composition="anchor-split"):
    """Derive a series identity from the manhwa's OWN artwork.

    Proposed, not approved: until the operator approves it this is a draft, so
    a bad auto-derivation never silently becomes a series' permanent look.
    """
    covers = cover_candidates(pdir)
    panels = [p["file"] for p in rank_panels(pdir, 4)]
    pal = extract_palette(covers + panels)
    return {
        "version": STYLE_VERSION,
        "series": (meta or {}).get("series") or "",
        "aliases": [],
        "anchor_images": [os.path.relpath(c, pdir) for c in covers[:1]],
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
                            composition=(pack or {}).get("composition", "anchor-split"))
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


def build_concepts(pdir, meta, style, title="", n=4):
    """3-5 chapter concepts, all built on the SAME approved series style.

    Only three things vary between chapters by design: the focal panel, the
    chapter badge, and the hook text. Everything else — palette, composition,
    badge style, typography — comes from the style pack, which is what makes a
    playlist of these look like one series.
    """
    panels = rank_panels(pdir, 8)
    chapter = str((meta or {}).get("chapter") or "").strip()
    hook = hook_from_title(title) if title else ""
    pal = (style or {}).get("palette", {})
    comp = (style or {}).get("composition", "anchor-split")
    inherited = bool((style or {}).get("approved"))
    anchors = (style or {}).get("anchor_images") or []
    out = []
    if not panels:
        return out

    # Concept 1 — the series anchor beside the chapter's strongest panel.
    # This is the recurring shape that makes uploads recognisable.
    plans = [
        ("Series anchor + chapter hook", comp, panels[0], bool(hook),
         "The series anchor keeps the upload recognisable in a feed; the "
         "chapter panel supplies what is new."),
    ]
    if len(panels) > 1:
        plans.append(("Full-bleed hook panel", "hero-focus", panels[1], bool(hook),
                      "One focal moment at full bleed reads fastest at sidebar size."))
    if len(panels) > 2:
        plans.append(("Textless character focus", comp, panels[2], False,
                      "No overlay: relies on the art alone, useful when the "
                      "title already carries the hook."))
    if len(panels) > 3:
        plans.append(("Badge-forward variant", "badge-stack", panels[3], bool(hook),
                      "Chapter number is the loudest element — helps serial "
                      "viewers find the next part."))
    if len(panels) > 4:
        plans.append(("Alternate moment", comp, panels[4], bool(hook),
                      "A different beat from the same chapter, in the same "
                      "series style."))

    for i, (name, composition, panel, use_text, why) in enumerate(plans[:max(3, min(n, 5))]):
        out.append({
            "id": "c%d" % i,
            "name": name,
            "type": _dominant_type(panel),
            "composition": composition,
            "focal_panel": panel["panel_id"],
            "focal_file": panel["file"],
            "focal_why": panel["why"],
            "panel_score": panel["score"],
            "anchor_image": anchors[0] if anchors and composition == "anchor-split" else None,
            "chapter": chapter,
            "badge": ((style or {}).get("badge") or {}).get("label", "CH"),
            "overlay_text": hook if use_text else "",
            "text_zone": (style or {}).get("text_zone", "lower-left"),
            "palette": pal,
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
        concepts.sort(key=lambda c: -c["score"]["total"])
        concepts[0]["recommended"] = True
    return concepts


# ========================================================= COMPOSITION
def _cover_crop(im, box_w, box_h):
    """Fill the box, centred, without distorting — letterboxing a thumbnail
    wastes the little space it has."""
    from PIL import Image
    sw, sh = im.size
    if not sw or not sh:
        return im.resize((box_w, box_h))
    scale = max(box_w / sw, box_h / sh)
    nw, nh = max(1, int(sw * scale)), max(1, int(sh * scale))
    im = im.resize((nw, nh), Image.LANCZOS)
    left, top = (nw - box_w) // 2, 0        # bias to the TOP: faces sit high
    return im.crop((left, top, left + box_w, top + box_h))


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

    def place(path, box):
        if not (path and os.path.exists(path)):
            return False
        try:
            with Image.open(path) as src:
                im = src.convert("RGBA")
                img.alpha_composite(_cover_crop(im, box[2] - box[0], box[3] - box[1]),
                                    (box[0], box[1]))
            return True
        except Exception:
            return False

    if comp == "anchor-split" and anchor and os.path.exists(anchor):
        split = int(width * 0.42)
        place(anchor, (0, 0, split, height))
        if not place(focal, (split, 0, width, height)):
            place(anchor, (split, 0, width, height))
        # a hard accent seam makes the two halves read as one deliberate design
        ImageDraw.Draw(img).rectangle([split - 4, 0, split + 1, height],
                                      fill=accent + (255,))
        _scrim(img, (0, int(height * 0.52), split, height))
    elif comp == "badge-stack":
        place(focal, (0, 0, width, height))
        _scrim(img, (0, int(height * 0.45), width, height), (0, 0, 0, 190))
    else:                                    # hero-focus
        place(focal, (0, 0, width, height))
        _scrim(img, (0, int(height * 0.5), width, height), (0, 0, 0, 185))

    draw = ImageDraw.Draw(img)
    pad = int(width * 0.035)

    # Chapter badge — the one element that must change every chapter.
    ch = str(concept.get("chapter") or "").strip()
    if ch:
        label = "%s %s" % (concept.get("badge") or "CH", ch)
        bf = _font(int(height * 0.085))
        tw = draw.textlength(label, font=bf)
        bh = int(height * 0.115)
        bw = int(tw + pad * 1.5)
        big = comp == "badge-stack"
        if big:
            bf = _font(int(height * 0.135))
            tw = draw.textlength(label, font=bf)
            bh = int(height * 0.175)
            bw = int(tw + pad * 1.6)
        bx, by = pad, height - bh - pad
        draw.rectangle([bx, by, bx + bw, by + bh], fill=accent + (255,))
        draw.text((bx + bw / 2, by + bh / 2), label, font=bf,
                  fill=readable_ink(accent), anchor="mm")

    # Hook text — short by construction, and clamped again here.
    txt = validate_hook(concept.get("overlay_text") or "")["text"]
    if txt:
        zone_w = (int(width * 0.42) - pad * 2 if comp == "anchor-split"
                  else width - pad * 2)
        tf, lines = _fit_text(draw, txt, zone_w, start=int(height * 0.16),
                              floor=int(height * 0.068))
        lh = int(tf.size * 1.06)
        block = lh * len(lines)
        badge_h = int(height * (0.175 if comp == "badge-stack" else 0.115))
        ty = height - badge_h - pad * 2 - block
        # A dark stroke keeps the text legible over any artwork beneath it.
        stroke = max(2, int(height * 0.008))
        for i, line in enumerate(lines):
            draw.text((pad, max(pad, ty) + i * lh), line, font=tf, fill=ink + (255,),
                      stroke_width=stroke, stroke_fill=(0, 0, 0, 255))

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
