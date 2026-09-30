"""P2 — direct speech, 2-3 per chapter, one narrator (docs/craft_reconciliation.md).

Pins: candidates come only from tail/position attributions; the model picks by
NUMBER and its speaker must be a name the chapter summary uses (labels drift
between panels); at most 3 per chapter; each scene is told exactly what it may
quote; stray quotes and a chapter over the cap are found in code; a split line
counts once; quotes and harsh profanity never reach the voice while mild words
do; the segmenter keeps a shouted line's punctuation; both engines carry the
same rule text.

Run: python3 test_direct_speech.py
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.join(HERE, "..")
sys.path[:0] = [HERE, RECAP]

import direct_speech as DS
import speech_text as ST
import beat_segmenter as BS
import narrate
import claude_plus as PLUS

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def L(text, speaker, basis="tail", typ="speech"):
    return {"text": text, "type": typ, "speaker": speaker,
            "speaker_basis": basis, "lang": "en"}


PANELS = [
    {"panel_id": "p1", "lines": [L("GET OUT OF MY HOUSE!", "the old man", typ="shout"),
                                 L("He won't shoot.", "the boy", basis="context", typ="thought")]},
    {"panel_id": "p2", "lines": [L("One more step and you leave through the window.", "the old man")]},
    {"panel_id": "p3", "lines": [L("...", "the boy"),
                                 {"text": "creaking", "type": "sfx", "speaker": None,
                                  "speaker_basis": None, "lang": "ko"}]},
    {"panel_id": "p4", "lines": [L("I'll take the book by force.", "the wild-haired man", basis="position")]},
    {"panel_id": "p5", "lines": [L("Fine. Stay, then.", "the old man")]},
]
SUMMARY = "Hector, a gruff old hunter, catches Jin reading his book and aims a crossbow."


def main():
    # ---------------------------------------------------------- candidates
    c = DS.candidates(PANELS)
    check("only tail/position lines of 2+ words are candidates",
          [x["text"] for x in c] == ["GET OUT OF MY HOUSE!",
                                     "One more step and you leave through the window.",
                                     "I'll take the book by force.", "Fine. Stay, then."])
    check("candidates are numbered in chapter order", [x["n"] for x in c] == [1, 2, 3, 4])

    # ------------------------------------------------------------ validate
    picks = [{"n": 2, "speaker": "Hector", "why": "the threat"},
             {"n": 3, "speaker": "Jin", "why": "his answer"},
             {"n": 1, "speaker": "the old man", "why": "label, not a name"},
             {"n": 99, "speaker": "Hector"},
             {"n": 4, "speaker": "Hector"}, {"n": 4, "speaker": "Hector"},
             "junk"]
    v = DS.validate(picks, c, SUMMARY)
    check("a speaker that is only a panel label is rejected (the drift guard)",
          all(x["n"] != 1 for x in v))
    check("unknown numbers, duplicates and junk are dropped", [x["n"] for x in v] == [2, 3, 4])
    check("the speaker is the NAME from the summary", v[0]["speaker"] == "Hector")
    check("the text is the reader's exact text, never the model's",
          v[0]["text"] == "One more step and you leave through the window.")
    # live ch.44: the model wrote compound speakers and the chapter names nobody
    bs44 = "the protagonist uses his inventory. a spy on the rooftops. the Cursed Killing Star faction"
    comp = DS.validate([{"n": 2, "speaker": "The Masked Ninja (The Spy)"},
                        {"n": 3, "speaker": "The Cursed Killing Star (The Assassin Duo)"}], c, bs44)
    check("a compound speaker passes when any part is a character the summary names",
          [(x["n"], x["speaker"]) for x in comp] == [(2, "The Spy"), (3, "The Cursed Killing Star")])
    check("...but a bare generic noun never does",
          DS.validate([{"n": 2, "speaker": "the man (the guy)"}], c, "a man and a guy") == [])
    many = [{"n": n, "speaker": "Hector"} for n in (1, 2, 3, 4)]
    check("never more than 3 per chapter", len(DS.validate(many, c, SUMMARY)) == 3)
    check("a name that only appears inside another word does not count",
          DS.validate([{"n": 2, "speaker": "Hec"}], c, SUMMARY) == [])
    asked = []
    got = DS.select(PANELS, SUMMARY,
                    lambda p: asked.append(p) or '{"picks":[{"n":2,"speaker":"Hector"}]}')
    check("select() asks once and returns the validated pick",
          len(asked) == 1 and len(got) == 1 and '2. [speech] the old man' in asked[0])
    check("a chapter with no attributable lines costs no model call",
          DS.select([{"panel_id": "x", "lines": []}], SUMMARY,
                    lambda p: 1 / 0) == [])

    # ------------------------------------------------------ scene blocks
    check("a scene without an approved line is told to quote nothing",
          "none" in DS.scene_block([]) and "no quotation" in DS.scene_block([]))
    blk = DS.scene_block(DS.for_panels(v, ["p2"]))
    check("a scene is given exactly its approved line and speaker",
          'Hector: "One more step' in blk and "Fine. Stay" not in blk)
    prompt = narrate.build_prompt(
        [{"panel_id": "p2", "visual_description": "aims a crossbow",
          "ocr_text": "x", "lines": PANELS[1]["lines"]}],
        direct_lines=DS.for_panels(v, ["p2"]))
    check("the approved line reaches the real Gemini scene prompt",
          "DIRECT SPEECH FOR THIS SCENE — the ONLY line" in prompt and 'Hector: "One more' in prompt)

    # ------------------------------------------------------------- audit
    units = [
        {"unit": 0, "panel_ids": ["p1", "p2"],
         "text": 'He did not lower it. "One more step," Hector said, "and you leave through the window." Nobody moved.'},
        {"unit": 1, "panel_ids": ["p5"], "text": 'The boy shrugged. "Whatever," he said.'},
    ]
    issues, summ = DS.audit(units, DS.for_panels(v, ["p2"]))
    check("an approved line split around its tag counts as ONE line",
          summ["approved_used"] == 1 and summ["quote_spans"] == 3)
    check("an unapproved quote is flagged on its own unit",
          [i["unit"] for i in issues if i["type"] == "unapproved_quote"] == [1])
    check("...with a fix the reviser can act on", "reported narration" in issues[0]["fix"])
    many_units = [{"unit": i, "panel_ids": ["zz"], "text": f'"Stray line number {i}," he said.'}
                  for i in range(4)]
    iss2, s2 = DS.audit(many_units, [])
    check("a chapter over the cap gets a chapter_quote_cap issue",
          any(i["type"] == "chapter_quote_cap" for i in iss2) and s2["lines_quoted"] == 4)
    ok_units = [{"unit": 0, "panel_ids": ["p2"],
                 "text": '"One more step and you leave through the window," Hector said.'}]
    check("a clean chapter has no issues", DS.audit(ok_units, DS.for_panels(v, ["p2"]))[0] == [])

    # --------------------------------------------------- what gets voiced
    s = ST.speakable('"One more step," he said, "and you leave."')
    check("quotation marks never reach the voice", '"' not in s and "“" not in ST.speakable("“Go.”"))
    check("...and the word 'quote' is never introduced", "quote" not in s.lower())
    check("mild profanity is kept verbatim",
          ST.speakable("Damn it, you bastard, what the hell.") == "Damn it, you bastard, what the hell.")
    check("harsher words are softened, keeping case",
          ST.speakable("What the fuck? Fucking hell. SHIT.") == "What the hell? Freaking hell. CRAP.")
    check("a written stutter is voiced as the clean word (ch.44 'C-Cursed' -> 'see')",
          ST.speakable('"C-Cursed Killing Star? WH-WHAT? I-IS it?"') == "Cursed Killing Star? WHAT? IS it?")
    check("...but a real hyphenated word is untouched",
          ST.speakable("A well-known ex-soldier.") == "A well-known ex-soldier.")
    check("slurs are dropped, and the gap tidied",
          ST.speakable("Shut up, retard.") == "Shut up.")
    import tts
    check("the SDK path voices the same speakable text", tts._clean_for_tts('"Run," she said.') == "Run, she said.")
    src = open(os.path.join(HERE, "server.py")).read()
    check("the REST TTS path applies speakable before the cache key",
          src.index("speech_text.speakable(text)") < src.index('ck = hashlib.sha1(f"{_TTS_VOICE}|{text}"'))

    # ------------------------------------------------------ segmentation
    beats = [b["text"] for b in BS.segment_beats('He ran for the gate. "Go!" She followed him out.')]
    check("a short quoted line keeps its '!' when merged", beats[0].endswith('"Go!"'))
    check("the sentence after a closing quote starts a new beat", beats[1] == "She followed him out.")
    check("a quote with its tag stays one beat",
          len(BS.segment_beats('"Get down!" Mia screamed, already diving for the floor.')) == 1)

    # ------------------------------------------------------- both engines
    gem = narrate.build_prompt([{"panel_id": "a", "visual_description": "x", "ocr_text": ""}])
    check("both engines carry the SAME rule text",
          DS.RULE_TEXT in gem and DS.RULE_TEXT in PLUS.SCRIPT_SYSTEM)
    check("the rule forbids a second voice and a tonal costume, explicitly",
          "NEVER BY A SECOND VOICE" in DS.RULE_TEXT and "YOUR OWN" in DS.RULE_TEXT
          and "impression" in DS.RULE_TEXT and "accent" in DS.RULE_TEXT)
    check("the old reported-speech-only ban is gone from both engines",
          "never quotation marks" not in gem and "NEVER use quotation" not in PLUS.SCRIPT_SYSTEM)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
