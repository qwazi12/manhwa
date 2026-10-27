"""Narration must never contain the language model talking about its task.

Mercenary ch.100 (2026-10-26) reached a rendered video ending with "Would you
like a summary of the next chapter?" — a chatbot reply that slipped into the
script. Nothing checked for it.

Patterns are deliberately narrow: recap narration legitimately quotes
characters ("Would you like to die?"), so only phrasing that is about the
task itself (summary, narration, recap, being an AI, offering more help)
matches.
"""
import re

PATTERNS = [
    r"\bas an ai\b",
    r"\b(?:i am|i'm) an ai\b",
    r"\blanguage model\b",
    r"\bwould you like (?:me to|a summary|a recap|another|the next (?:chapter|part)|more)\b",
    r"\bsummary of the next chapter\b",
    r"\b(?:here is|here's|below is) (?:the|a|your) (?:narration|recap|summary|script|rewrite|revised version)\b",
    r"\blet me know if you\b",
    r"\b(?:i hope this helps|happy to help)\b",
    r"\b(?:shall|should) i (?:continue|write|summari[sz]e|proceed)\b",
    r"\bnarration for (?:panel|chapter|this panel)\b",
]
_RE = re.compile("|".join(PATTERNS), re.I)


def is_assistant_text(text):
    return bool(_RE.search(text or ""))


def assistant_lines(segs, seg_indexes=None):
    """[(seg_index, beat_index, text)] for narration that reads like the model
    talking to its user. seg_indexes limits the search (None = all)."""
    want = None if seg_indexes is None else set(seg_indexes)
    out = []
    for s in segs or []:
        if want is not None and s.get("seg_index") not in want:
            continue
        for b in s.get("beats") or []:
            if is_assistant_text(b.get("text")):
                out.append((s.get("seg_index"), b.get("index"), (b.get("text") or "").strip()))
    return out
