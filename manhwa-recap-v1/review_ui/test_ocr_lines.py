"""P0 (drawn non-English SFX) + P1 (per-bubble lines) — docs/craft_reconciliation.md.

P0: the reader copied Korean SFX glyphs into ocr_text; 23-29% of the segments
counted as dialogue were such glyphs, padding the word budget 7 words each and
reaching the writer's prompt. P1: the reader returns one record per bubble
with type + speaker + how the speaker was decided; old projects without it
must keep working byte-for-byte.

Run: python3 test_ocr_lines.py
"""
import json
import os
import re
import sys
import tempfile

HERE = os.path.dirname(os.path.abspath(__file__))
RECAP = os.path.join(HERE, "..")
sys.path[:0] = [HERE, RECAP, os.path.join(RECAP, "..", "panel-describe")]

import ocr_lines as OL
import narrate
import claude_plus as PLUS
import describe

R = []
HANGUL = re.compile(r"[가-힯ᄀ-ᇿ㄰-㆏]")


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    # ---------------------------------------------------------------- P0
    live = "탕 / HEY, JIN! / 타앙..."          # a real swordmasters_1 panel
    check("a drawn Hangul SFX is recognised", OL.is_foreign_sfx("타앙..."))
    check("...but English, and mixed lines, are not",
          not OL.is_foreign_sfx("HEY, JIN!") and not OL.is_foreign_sfx("KUGH! 퍽"))
    check("speech_segments drops drawn SFX", OL.speech_segments(live) == ["HEY, JIN!"])
    check("prompt_ocr shows the event, never the glyphs",
          OL.prompt_ocr(live) == "[sound effect] / HEY, JIN! / [sound effect]"
          and not HANGUL.search(OL.prompt_ocr(live)))
    check("a run of SFX collapses to one placeholder",
          OL.prompt_ocr("탕 / 퍽 / RUN!") == "[sound effect] / RUN!")

    legacy = [{"panel_id": "p1", "visual_description": "a punch lands",
               "ocr_text": live},
              {"panel_id": "p2", "visual_description": "footsteps",
               "ocr_text": "저벅"}]
    check("the Gemini word budget no longer counts drawn SFX (was 4, now 1)",
          narrate.dialogue_lines(legacy) == 1)
    prompt = narrate.build_prompt(legacy)
    check("...and the Gemini scene prompt carries no Hangul", not HANGUL.search(prompt))
    check("...but still tells the writer a sound happened", "[sound effect]" in prompt)
    check("the Claude engine's count ignores three Hangul syllables",
          PLUS.dialogue_lines([{"ocr_text": "탕 / 타앙 / 퍽"}]) == 0
          and PLUS.dialogue_lines([{"ocr_text": "WHO SENT YOU HERE?"}]) == 1)

    # ---------------------------------------------------------------- P1
    raw = [
        {"text": "GET OUT OF MY HOUSE!", "type": "shout", "speaker": "the old man",
         "speaker_basis": "tail", "lang": "en"},
        {"text": "He won't shoot.", "type": "thought", "speaker": "Jin",
         "speaker_basis": "context", "lang": "en"},
        {"text": "creaking wood", "type": "sfx", "speaker": "", "speaker_basis": "tail",
         "lang": "ko"},
        {"text": "타앙", "type": "speech", "speaker": "x", "speaker_basis": "tail"},
        {"text": "The house had been empty for years.", "type": "caption",
         "speaker": "narrator", "speaker_basis": "tail", "lang": "en"},
        {"text": "", "type": "speech"},
        {"text": "ok", "type": "banter", "speaker": "Jin", "speaker_basis": "vibes"},
        "garbage",
    ]
    lines = OL.normalize_lines(raw)
    check("normalize keeps the real lines and drops empties/garbage", len(lines) == 6)
    check("copied Hangul is forced to an sfx placeholder, whatever it was labelled",
          lines[3]["type"] == "sfx" and lines[3]["text"] == OL.SFX_PLACEHOLDER)
    check("captions and SFX never carry a speaker",
          lines[2]["speaker"] is None and lines[4]["speaker"] is None)
    check("an unknown type falls back to speech, an unknown basis to 'guess'",
          lines[5]["type"] == "speech" and lines[5]["speaker_basis"] == "guess")
    check("only tail/position attributions may be quoted later",
          OL.is_attributable(lines[0]) and not OL.is_attributable(lines[1])
          and not OL.is_attributable(lines[5]))
    ocr = OL.ocr_from_lines(lines)
    check("ocr_text rebuilt from lines leaves out drawn SFX, keeps the words",
          "GET OUT OF MY HOUSE!" in ocr and "creaking wood" not in ocr
          and not HANGUL.search(ocr))
    panel = {"panel_id": "p3", "visual_description": "old man aims a crossbow",
             "ocr_text": ocr, "lines": lines}
    check("dialogue_count with lines counts bubbles and captions, never SFX",
          OL.dialogue_count(panel) == 4)   # shout + thought + caption + "ok"
    block = OL.prompt_block(panel)
    check("the writer sees speaker and basis for each bubble",
          '[shout] the old man (tail): "GET OUT OF MY HOUSE!"' in block
          and "[sfx]" in block and '[caption] "The house' in block)
    check("...and it reaches the real Gemini prompt",
          '[shout] the old man (tail)' in narrate.build_prompt([panel]))
    only_sfx = {"panel_id": "p4", "visual_description": "a door slams",
                "ocr_text": "", "lines": [{"text": "door slam", "type": "sfx",
                                           "speaker": None, "speaker_basis": None,
                                           "lang": "ko"}]}
    check("a panel whose only text is a drawn SFX still shows the writer the sound",
          "[sfx] door slam" in narrate.build_prompt([only_sfx]))
    check("a project described before lines existed renders exactly as before",
          'Dialogue/text visible in this panel: "HEY, JIN!"'
          in narrate.build_prompt([{"panel_id": "old", "visual_description": "d",
                                    "ocr_text": "HEY, JIN!"}]))

    # ------------------------------------------ the reader's record (no API)
    saved = describe.describe_with_gemini
    try:
        describe.describe_with_gemini = lambda path, key, model, prompt=None: (
            "탕 / GET OUT!", "old man shouting", raw)
        tmp = tempfile.mkdtemp()
        img = os.path.join(tmp, "page001_panel_001.png")
        from PIL import Image
        Image.new("RGB", (40, 30), "white").save(img)
        rec = describe.describe_panel(img, "k", "m")
        check("describe_panel stores normalised lines",
              rec["ok"] and len(rec["lines"]) == 6 and rec["lines"][0]["type"] == "shout")
        check("...and derives ocr_text from them, with no drawn SFX",
              rec["ocr_text"] == OL.ocr_from_lines(rec["lines"])
              and not HANGUL.search(rec["ocr_text"]))
        describe.describe_with_gemini = lambda path, key, model, prompt=None: (
            "탕 / GET OUT!", "old man shouting", None)
        rec = describe.describe_panel(img, "k", "m")
        check("a reply without lines still strips drawn SFX from ocr_text",
              rec["ocr_text"] == "GET OUT!" and rec["lines"] == [])
    finally:
        describe.describe_with_gemini = saved
    check("the reader's prompt forbids copying non-English SFX lettering",
          "NEVER copy, romanize or translate" in describe.VISION_PROMPT
          and '"lines"' in describe.VISION_PROMPT)
    check("the Claude reader asks for the same lines, schema-enforced",
          "lines" in PLUS.DESCRIBE_SCHEMA["properties"]["panels"]["items"]["required"]
          and "NEVER copy" in PLUS.DESCRIBE_SYSTEM)

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print(f"\n{n}/{len(R)} passed")
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
