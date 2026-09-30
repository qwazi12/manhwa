"""Script text -> what the narrator actually says. Applied at the TTS call only.

docs/craft_reconciliation.md §7g + owner decision Q4 (§12):
  * quotation marks are a page convention with nothing to sound like — the
    attribution tag and the sentence rhythm carry a direct line, so the marks
    are removed before voicing (never the word "quote");
  * mild profanity is kept verbatim; harsher words are softened for platform
    safety. The SCRIPT keeps the page's words (the board shows them); only the
    AUDIO changes.

The word lists below are the policy. They are deliberately short and explicit
so the owner can review and edit them in one place.
"""
import re

# Kept as written (mild): damn, hell, crap, ass, bastard, bloody, piss, screw.

# Harsher words -> softer spoken form. Order matters: longer phrases first.
SOFTEN = [
    (r"\bwhat the fuck\b", "what the hell"),
    (r"\bmotherfuck(?:er|ers|ing)\b", "bastard"),
    (r"\bfuck(?:ing|in')\b", "freaking"),
    (r"\bfuck(?:ed)\b", "screwed"),
    (r"\bfuck(?:er|ers)\b", "bastard"),
    (r"\bfuck\b", "screw"),
    (r"\bbullshit\b", "nonsense"),
    (r"\bshit(?:ty)\b", "lousy"),
    (r"\bshit\b", "crap"),
    (r"\bbitch(?:es)?\b", "wretch"),
    (r"\bcunt\b", "bastard"),
    (r"\bdick(?:head)?\b", "jerk"),
]
_SOFTEN = [(re.compile(p, re.I), r) for p, r in SOFTEN]

# Slurs are never voiced, softened or not: the word is dropped.
DROP = [r"\bn[i1]gg(?:a|er)s?\b", r"\bf[a4]gg?ots?\b", r"\bretard(?:ed|s)?\b"]
_DROP = [re.compile(p, re.I) for p in DROP]

_QUOTES = str.maketrans({'"': None, "“": None, "”": None})


def _match_case(src, repl):
    if src.isupper():
        return repl.upper()
    if src[:1].isupper():
        return repl[:1].upper() + repl[1:]
    return repl


def soften(text):
    for rx, repl in _SOFTEN:
        text = rx.sub(lambda m, r=repl: _match_case(m.group(0), r), text)
    for rx in _DROP:
        text = rx.sub("", text)
    return text


# A written stutter — "C-Cursed", "WH-WHAT", "I-IS" — is voiced letter by
# letter: Chirp read "C-Cursed Killing Star" as roughly "see, Cursed…" on the
# ch.44 listening test. The prefix is dropped for the audio; the script keeps
# it. (Only when the prefix really repeats the word's opening letters.)
_STUTTER = re.compile(r"\b([A-Za-z]{1,2})-(?=\1)", re.I)


def speakable(text):
    """The words the voice should say for one beat of script text."""
    t = soften((text or "").translate(_QUOTES))
    t = _STUTTER.sub("", t)
    t = re.sub(r"\s+([,.!?;:])", r"\1", t)     # tidy a dropped word's gap
    t = re.sub(r"[,;:]+([.!?])", r"\1", t)      # "Shut up, <dropped>." -> "Shut up."
    return re.sub(r"\s{2,}", " ", t).strip()
