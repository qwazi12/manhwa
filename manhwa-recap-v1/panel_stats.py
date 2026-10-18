"""Image evidence for each panel: is it real art, a speech bubble / text card,
a near-blank fragment, or a credits card?

Why (owner, 2026-10-04, Murim 44 board): the junk filter only read the AI's
WORDS. "a spiky BURST speech bubble", "blood-red ENERGY on black", "GLOWING
embers in darkness" and "RAIN streaks in darkness" all contain words the keep
rule treats as a real scene, so bubble-only cards and near-black strips got
their own narration lines and 6–9 s on screen. The reverse also happened: a
900×3994 mountain establishing shot was DROPPED because its description said
"…extending down the panel". The picture itself settles both.

Measured on Murim 44's 145 panels (300 px thumbnails):
  * near-blank fragments (embers on black, black strips, white spacers, a
    faint red slash): luma std 0–13, no text. No real story panel on the
    timeline had std below 34.
  * bubble / text cards ("I THOUGHT IT WAS JUST A PERFORMANCE", "ELDER,"):
    ink 0.09–0.13 with OCR and no person in the description.
  * real art: ink 0.21–0.62 (the dark "glaring eyes" panel: ink 0.09 but
    std 34 and no text — still art).

Roles:
  art       on the timeline as before
  bubble    off the timeline; its dialogue is attached to the neighbouring art
            panel's text so the narration still covers it
  fragment  off the timeline (near-blank / low contrast, no text)
  credits   off the timeline, never narrated
"""

import json
import os
import re
import copy

FRAGMENT_STD = 20.0     # luma std below this, with no text = near-blank fragment
FRAGMENT_INK = 0.10     # ...and less ink than this (Murim fragments: ≤ 0.076)
BUBBLE_INK = 0.16       # text panel with less ink than this and no person = bubble/card
MIN_SIDE = 40           # a crop thinner than this is a sliver, whatever is in it
BANNER_WIDTH_DEV = 0.25 # page-1 panel this much wider/narrower than the chapter = site banner
THUMB = 300
# Big bold lettering on a plain ground has HIGH ink (0.36–0.55 on Mount Hua
# 180) — ink alone calls it art. The description still says what it is.
_TEXT_CARD = re.compile(r"\b(texts?|typography|lettering|letters|captions?|narration|"
                        r"(speech |thought |dialogue )?bubbles?)\b", re.I)
_CREDIT_OCR = re.compile(r"(asura|scans?\b|\.com\b|discord|\btl\s*[|:]|\bpr\s*[|:]|"
                         r"\bts\s*[|:]|translat|proofread|typeset|adaptation|storyboard|"
                         r"illustrat|original (story|work))", re.I)
_CREDITS = re.compile(r"\b(credits?|logo|title card|series title|watermark|translator|"
                      r"proofreader|typesetter|scanlat\w*)\b", re.I)
_SUBJECT = None


def _subject_re():
    global _SUBJECT
    if _SUBJECT is None:
        try:
            import matcher
            _SUBJECT = matcher._KEEP_SUBJECT
        except Exception:
            _SUBJECT = re.compile(r"\b(person|man|woman|boy|girl|figure|face|eyes|hand)\b", re.I)
    return _SUBJECT


def measure(path):
    """{ink, std, luma}: share of pixels clearly unlike the dominant colour,
    luma standard deviation and mean, on a small thumbnail."""
    from PIL import Image
    import numpy as np
    im = Image.open(path).convert("RGB")
    im.thumbnail((THUMB, THUMB))
    a = np.asarray(im).astype(int)
    q = (a // 32).reshape(-1, 3)
    vals, cnt = np.unique(q, axis=0, return_counts=True)
    bg = vals[cnt.argmax()] * 32 + 16
    ink = float((np.abs(a - bg).sum(axis=2) > 90).mean())
    luma = (a * [0.299, 0.587, 0.114]).sum(axis=2)
    return {"ink": round(ink, 3), "std": round(float(luma.std()), 1),
            "luma": round(float(luma.mean()), 1)}


_QUOTED = re.compile(r"[\"'‘’“”]([^\"'‘’“”]{1,120})[\"'‘’“”]")
_NEGATED = re.compile(r"\b(no|without|absent|lacking|nor)\b[^.;]*", re.I)
# Stricter than the matcher's keep lists on purpose: "a plain LIGHT background"
# and "GLOWING red text" are not places, and a serpent is somebody in frame.
_CREATURE = re.compile(r"\b(serpent|snake|dragon|beast|creature|monster|animal|bird|"
                       r"cat|dog|wolf|tiger|horse|fox|spirit|ghost|demon|heads?)\b", re.I)
_PLACE = re.compile(r"\b(room|wall|floor|ground|trees?|forest|woods|mountains?|cliff|slope|"
                    r"building|street|road|path|alley|palace|castle|temple|hall|corridor|"
                    r"cave|tunnel|vault|door|window|roof|rooftop|courtyard|plaza|city|town|"
                    r"village|field|sky|moon|water|river|sea|landscape|post)\b", re.I)


def _scene_re():
    return _PLACE


def _what_is_shown(desc):
    """The description minus quoted lettering ("the word 'ELDER'") and negated
    clauses ("No characters, scenery, or figures are present") — both used to
    read as a person being in frame."""
    return _NEGATED.sub(" ", _QUOTED.sub(" ", desc or ""))


SLIVER_MAX_H = 110      # px: a strip this thin and...
SLIVER_RATIO = 6.0      # ...this wide (w/h) is a slice, not a panel


def classify(panel, pix, chapter_width=None):
    ocr = (panel.get("ocr_text") or "").strip()
    has_text = len(re.findall(r"[A-Za-z]{2,}", ocr)) >= 1
    desc = panel.get("visual_description") or ""
    w, h = panel.get("width") or 0, panel.get("height") or 0
    if _CREDITS.search(desc) or _CREDIT_OCR.search(ocr):
        return "credits"
    if chapter_width and w and str(panel.get("panel_id", "")).startswith("page001") \
            and abs(w - chapter_width) / chapter_width > BANNER_WIDTH_DEV:
        return "credits"                # the aggregator's banner image on page 1
    if (w and w < MIN_SIDE) or (h and h < MIN_SIDE):
        return "fragment"
    if not has_text and pix["std"] < FRAGMENT_STD and pix["ink"] < FRAGMENT_INK:
        return "fragment"               # low contrast AND little ink (a smoke cloud has ink)
    if not has_text and h and w and h < SLIVER_MAX_H and w / h >= SLIVER_RATIO:
        # A thin full-width strip of art is a slice of a bigger picture (the
        # A Regressor's Tale ch.30 failure, 2026-10-04): never its own line.
        return "fragment"
    shown = _what_is_shown(desc)
    somebody = _subject_re().search(shown) or _CREATURE.search(shown)
    somewhere = _scene_re().search(shown)
    if has_text and not somebody and not somewhere and \
            (pix["ink"] < BUBBLE_INK or _TEXT_CARD.search(desc)):
        return "bubble"
    return "art"


def annotate(desc_path, crops_dir):
    """Add `pix` and `role` to every panel in descriptions.json. Cheap (no
    API calls); safe to re-run. Returns a count per role."""
    with open(desc_path, encoding="utf-8") as f:
        panels = json.load(f)
    widths = sorted(p.get("width") for p in panels if p.get("width"))
    chapter_w = widths[len(widths) // 2] if widths else None
    counts = {}
    for p in panels:
        path = os.path.join(crops_dir, p.get("file") or f"{p.get('panel_id')}.png")
        if not os.path.isabs(path) and not os.path.exists(path):
            path = os.path.join(crops_dir, os.path.basename(p.get("file") or "") or f"{p.get('panel_id')}.png")
        try:
            p["pix"] = measure(path)
        except Exception:
            p.pop("pix", None)
            p.pop("role", None)
            continue
        p["role"] = classify(p, p["pix"], chapter_w)
        counts[p["role"]] = counts.get(p["role"], 0) + 1
    tmp = desc_path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(panels, f, indent=2)
    os.replace(tmp, desc_path)
    return counts


def attach_bubble_dialogue(panels):
    """Ordered, typed transfer on the in-memory narration input only.

    Repeated calls restore original evidence first. An orphan stays explicit;
    no cross-page speaker or image assignment is fabricated.
    """
    import matcher
    import ocr_lines

    def page(pid):
        return (pid or "").split("_panel")[0]

    def usable(p):
        return p.get("role", "art") == "art" and p.get("ok", True) and not matcher.is_junk_panel(p)

    for p in panels:
        if "_dialogue_original" not in p:
            p["_dialogue_original"] = {"ocr_text": p.get("ocr_text", ""),
                                       "lines": copy.deepcopy(p.get("lines"))}
        p.update(copy.deepcopy(p["_dialogue_original"]))
        for k in ("dialogue_from", "dialogue_to", "dialogue_orphan"):
            p.pop(k, None)
        p["lines"] = ocr_lines.source_lines(p)
    moved, groups = {}, {}
    for i, b in enumerate(panels):
        if b.get("role") != "bubble":
            continue
        text = (b.get("ocr_text") or ocr_lines.ocr_from_lines(b["lines"])).strip()
        if not text:
            continue
        target = None
        for j, q in enumerate(panels[i + 1:], i + 1):
            if page(q.get("panel_id")) != page(b.get("panel_id")):
                break
            if usable(q):
                target = j
                break
        if target is None:
            for j in range(i - 1, -1, -1):
                q = panels[j]
                if page(q.get("panel_id")) != page(b.get("panel_id")):
                    break
                if usable(q):
                    target = j
                    break
        if target is None:
            b["dialogue_orphan"] = {"panel_id": b["panel_id"], "lines": copy.deepcopy(b["lines"]),
                                     "text": text, "reason": "No eligible story-art panel on the same page; placement needs review."}
            continue
        groups.setdefault(target, []).append((i, text, copy.deepcopy(b["lines"]), b["panel_id"]))
        moved[b["panel_id"]] = panels[target]["panel_id"]
        b["dialogue_to"] = panels[target]["panel_id"]
    for j, transfers in groups.items():
        target = panels[j]
        entries = sorted(transfers + [(j, target.get("ocr_text", ""), target["lines"], None)])
        target["ocr_text"] = " / ".join(text for _, text, _, _ in entries if text)
        target["lines"] = [line for _, _, lines, _ in entries for line in lines]
        target["dialogue_from"] = [pid for _, _, _, pid in entries if pid]
    return moved
