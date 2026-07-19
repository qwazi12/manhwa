"""
Segment a raw narration script into natural sentence-level beats.

One beat = one full sentence (or a couple of very short sentences merged),
so TTS voices a complete thought and the matcher aligns a whole semantic
unit to a panel. This replaces SRT-fragment chunking, which split
sentences mid-thought and produced unnatural narration.
"""

import re

MIN_BEAT_WORDS = 4      # sentences shorter than this merge into the previous beat
MAX_BEAT_WORDS = 45     # very long sentences may split on clause boundaries

# Abbreviations that should NOT be treated as sentence ends.
_ABBREV = {"mr", "mrs", "ms", "dr", "st", "vs", "etc", "e.g", "i.e"}


def _protect_abbreviations(text: str) -> str:
    for a in _ABBREV:
        text = re.sub(rf"\b{re.escape(a)}\.", a + "<DOT>", text, flags=re.IGNORECASE)
    return text


def _restore(text: str) -> str:
    return text.replace("<DOT>", ".")


def split_sentences(text: str):
    """Split on . ! ? followed by whitespace, keeping the punctuation."""
    text = _protect_abbreviations(" ".join(text.split()))
    parts = re.split(r"(?<=[.!?])\s+", text)
    return [_restore(p).strip() for p in parts if p.strip()]


def _clause_split(sentence: str):
    """Split an over-long sentence on strong internal boundaries
    (semicolons, ' — ', or ', and/', but ') so no single beat is a wall
    of text. Falls back to the whole sentence if no clean seam exists."""
    seams = re.split(r"(?<=;)\s+|\s+—\s+|(?<=,)\s+(?=and\b|but\b|so\b)", sentence)
    seams = [s.strip() for s in seams if s.strip()]
    return seams if len(seams) > 1 else [sentence]


def segment_beats(script_text: str):
    """
    Return a list of beat dicts:
        {"index": int, "text": str, "word_count": int}
    Rules:
      - one sentence per beat,
      - sentences under MIN_BEAT_WORDS merge into the previous beat,
      - sentences over MAX_BEAT_WORDS split on clause seams.
    """
    raw = []
    for sent in split_sentences(script_text):
        if len(sent.split()) > MAX_BEAT_WORDS:
            raw.extend(_clause_split(sent))
        else:
            raw.append(sent)

    # merge too-short fragments into the previous beat
    merged = []
    for s in raw:
        if merged and len(s.split()) < MIN_BEAT_WORDS:
            merged[-1] = merged[-1].rstrip(".!?") + ". " + s
        else:
            merged.append(s)
    # also merge a too-short FIRST beat forward
    if len(merged) >= 2 and len(merged[0].split()) < MIN_BEAT_WORDS:
        merged[1] = merged[0].rstrip(".!?") + ". " + merged[1]
        merged.pop(0)

    return [{"index": i, "text": t, "word_count": len(t.split())}
            for i, t in enumerate(merged)]


def segment_beats_scenes(scenes):
    """Provenance-aware segmentation (B1): `scenes` is the structured script
    [{"scene_id", "panel_ids", "text"}, ...] written by narrate. Each scene's
    text is segmented independently, beats are re-indexed globally, and every
    beat carries its scene_id + panel_ids so the matcher can constrain
    placement to the panels the text was actually written about."""
    beats, idx = [], 0
    for sc in scenes:
        for b in segment_beats(sc["text"]):
            b["index"] = idx
            b["scene_id"] = sc["scene_id"]
            b["panel_ids"] = list(sc.get("panel_ids") or [])
            beats.append(b)
            idx += 1
    return beats


if __name__ == "__main__":
    import sys, json
    text = open(sys.argv[1]).read() if len(sys.argv) > 1 else sys.stdin.read()
    beats = segment_beats(text)
    for b in beats:
        print(f"[{b['index']:>3}] ({b['word_count']:>2}w) {b['text']}")
    print(f"\n{len(beats)} beats total")
