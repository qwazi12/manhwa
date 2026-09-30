"""Craft rules from the storytelling guide — docs/craft_reconciliation.md §4 (P3).

Approved by the owner 2026-09-29, with Q1 decided: follow the guide's tone —
no asides to the listener, no meta jokes, no questions to the audience.

One copy of the wording, included by both engines' style contracts, so the
Gemini and Claude narrations can never drift apart. The engines number their
contracts differently, so rules are rendered with a caller-chosen first number.
"""

# Appended to the continuity rule (narrate 4b / claude 6) — bridging is part of
# continuity, not a separate rule (§4, "9").
BRIDGE = ("Carry the listener across a change of moment with motion, time or "
          "sound — \"by the time he reached the door…\", a noise that starts in "
          "one moment and lands in the next — never with layout language. Mark a "
          "flashback with a time phrase (\"Years earlier…\") and return with one "
          "(\"Now…\").")

_RULES = [
    ("PACE FOLLOWS THE PULSE",
     "Slow down for reveals, grief, awe and the beat before danger: longer "
     "sentences, one detail at a time. Speed up for fights, chases and "
     "arguments: short sentences, fewer clauses. Three identical hits become one "
     "rhythmic line (\"He struck. He struck again. The wall held.\"). Escalate "
     "one thing per beat — speed, stakes or scale — and save all three for the "
     "peak. End a scene on its most open beat: an unanswered line, a reaching "
     "hand, a sound with no source."),
    ("ONE SENSE PER NEW PLACE",
     "The first time the story arrives somewhere new, give it ONE strong "
     "non-visual detail the art clearly implies — heat, damp, a smell, a sound. "
     "Texture only: never a new object, event, person or rule. Then refer back to "
     "the place in a phrase; do not describe it again."),
    ("PROTECT THE REVEALS",
     "The chapter summary marks reveals and callbacks. Never hint at a marked "
     "reveal before the scene where it lands. When an object or line will pay off "
     "later, give it one clean mention the first time so the payoff clicks."),
    ("SOUND EFFECTS ARE EVENTS, NOT TEXT",
     "Perform a big sound effect as the event it marks (\"the door came off its "
     "hinges\"), fold small ones into the action, and never read out, spell, "
     "romanize or translate sound-effect lettering — least of all lettering in "
     "Korean, Japanese or Chinese script. A panel line marked [sfx] or "
     "[sound effect] is an event to narrate, never words to say."),
    ("CONFIDENT, NOT CUTE",
     "Talk across to a sharp older-teen listener: cut what the art already makes "
     "obvious, play fear, loss and menace straight, and let humor come only from "
     "timing and dry understatement — never a joke that breaks a heavy moment, "
     "never a pop-culture reference, never slang for its own sake. Let the "
     "narration take on the attitude of the character in focus when it fits. "
     "Never speak to the listener: no asides, no \"you\", no \"let's\", no meta "
     "comments about the story, and no questions to the audience — a question "
     "for suspense belongs to a character."),
    ("LAND THE ENDING",
     "The final scene slows down and ends on a concrete image or a line, then "
     "stops. No summary, no outro, no \"what will happen next\"."),
]


def rules(start):
    """The craft rules numbered from `start` (narrate: 8, claude: 12)."""
    return "\n".join(f"{start + i}. {title}. {body}"
                     for i, (title, body) in enumerate(_RULES))


# Added to the chapter-summary (beatsheet) prompt so rule "PROTECT THE
# REVEALS" has something to protect.
REVEALS_DIRECTIVE = (
    "6. Mark PROTECTED REVEALS and CALLBACKS explicitly: list each twist, hidden "
    "identity or hidden ability, with the panel where it lands, and each object "
    "or line that pays off later, with where it first appears and where it pays "
    "off. Scene writers use this to avoid hinting at a reveal early.")
