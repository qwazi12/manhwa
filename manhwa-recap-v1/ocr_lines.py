"""Panel text, cleaned and structured — shared by both engines and the reader.

Two jobs, one module (docs/craft_reconciliation.md P0 + P1):

P0 — sound-effect lettering. The reader used to copy Korean/Japanese/Chinese
SFX glyphs into `ocr_text` ("탕 / HEY, JIN! / 타앙..."). Measured on local
projects, 23-29% of the segments counted as "dialogue" were such glyphs: each
inflated the word budget by 7 words, added noise to the matcher's strongest
signal, and left it to the writer to not read them out. `speech_segments`
drops them from counting; `prompt_ocr` shows them to the writer as
"[sound effect]" so the event survives but the lettering never can.

P1 — per-bubble lines. The reader now also returns `lines`: one record per
bubble with its type, speaker and how the speaker was decided. Direct speech
(the next phase) is unusable without it — a quoted line credited to the wrong
character is worse than a reported one. Records without `lines` (every project
described before this change) keep working exactly as before.
"""
import re

# Hangul (jamo, compatibility jamo, syllables), kana, CJK unified ideographs.
_CJK = re.compile(r"[ᄀ-ᇿ㄰-㆏가-힯぀-ヿ一-鿿]")
_LATIN = re.compile(r"[A-Za-z]")

LINE_TYPES = ("speech", "shout", "whisper", "thought", "caption", "sfx")
SPEAKER_BASES = ("tail", "position", "context", "guess")
# Only these bases are trustworthy enough to put words in a named mouth.
ATTRIBUTABLE = ("tail", "position")
SFX_PLACEHOLDER = "[sound effect]"


def is_foreign_sfx(segment):
    """Lettering in a non-Latin script with no Latin letters at all — a drawn
    sound effect, never dialogue in an English-translated chapter."""
    s = segment or ""
    return bool(_CJK.search(s)) and not _LATIN.search(s)


def _segments(ocr_text):
    return [s.strip() for s in (ocr_text or "").split("/") if s.strip()]


def speech_segments(ocr_text):
    """The ' / '-joined OCR segments that are words, not drawn SFX."""
    return [s for s in _segments(ocr_text) if not is_foreign_sfx(s)]


def prompt_ocr(ocr_text):
    """OCR text safe to show a writer: foreign SFX become one placeholder per
    run, so the event is still visible but no glyph can be read aloud."""
    out = []
    for s in _segments(ocr_text):
        if is_foreign_sfx(s):
            if not out or out[-1] != SFX_PLACEHOLDER:
                out.append(SFX_PLACEHOLDER)
        else:
            out.append(s)
    return " / ".join(out)


def normalize_lines(raw):
    """Validate the reader's `lines` array against an allowlist. Anything
    malformed is dropped or coerced to the most conservative value — an
    unknown speaker basis becomes 'guess', which can never be quoted."""
    out = []
    for item in raw if isinstance(raw, list) else []:
        if not isinstance(item, dict):
            continue
        text = str(item.get("text") or "").strip()
        if not text:
            continue
        typ = str(item.get("type") or "").strip().lower()
        if typ not in LINE_TYPES:
            typ = "speech"
        lang = str(item.get("lang") or "en").strip().lower()[:5] or "en"
        if is_foreign_sfx(text):
            # the reader was told to describe, not copy — enforce it here too
            typ, text = "sfx", SFX_PLACEHOLDER
            lang = lang if lang != "en" else "other"
        speaker = item.get("speaker")
        speaker = str(speaker).strip() if speaker not in (None, "", "null") else None
        basis = str(item.get("speaker_basis") or "").strip().lower()
        if basis not in SPEAKER_BASES:
            basis = "guess"
        if typ in ("caption", "sfx"):
            speaker, basis = None, None
        out.append({"text": text, "type": typ, "speaker": speaker,
                    "speaker_basis": basis, "lang": lang})
    return out


def ocr_from_lines(lines):
    """The legacy `ocr_text` string, rebuilt from lines, for the matcher and
    every consumer that predates `lines`. Foreign SFX are left out."""
    return " / ".join(l["text"] for l in lines
                      if not (l["type"] == "sfx" and l["lang"] != "en"))


def is_attributable(line):
    """May this line be put in a named character's mouth (direct speech)?"""
    return (line.get("type") in ("speech", "shout", "whisper", "thought")
            and bool(line.get("speaker"))
            and line.get("speaker_basis") in ATTRIBUTABLE)


def dialogue_count(panel):
    """Text units that earn narration budget on one panel: every bubble and
    caption, never a sound effect. Captions stay in — the legacy count
    included them, and narration boxes carry story; only SFX were the
    inflation (P0). Falls back to the legacy segments minus drawn SFX."""
    lines = panel.get("lines")
    if isinstance(lines, list) and lines:
        return sum(1 for l in lines if l.get("type") != "sfx")
    return len(speech_segments(panel.get("ocr_text")))


def prompt_block(panel, indent="  "):
    """The panel's text as the writer sees it. With `lines`: one row per
    bubble — type, speaker and basis — so attribution is explicit. Without:
    the legacy one-line form, with drawn SFX replaced by a placeholder."""
    lines = panel.get("lines")
    if isinstance(lines, list) and lines:
        rows = []
        for l in lines:
            if l["type"] == "sfx":
                rows.append(f"{indent}  [sfx] {l['text']}")
            elif l["type"] == "caption":
                rows.append(f'{indent}  [caption] "{l["text"]}"')
            else:
                who = l.get("speaker") or "unknown speaker"
                rows.append(f'{indent}  [{l["type"]}] {who} ({l.get("speaker_basis") or "guess"}): "{l["text"]}"')
        return f"{indent}Lines on this panel:\n" + "\n".join(rows)
    text = prompt_ocr(panel.get("ocr_text"))
    return f'{indent}Dialogue/text visible in this panel: "{text}"' if text else ""
