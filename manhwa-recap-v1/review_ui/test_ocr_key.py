"""The OCR field is `ocr_text`, and reading the wrong key costs real money.

`claude_plus.describe_plus` stores the transcription as **ocr_text**. The
benchmark endpoint counted `d["ocr"]`, which is always absent, so it reported
with_ocr=0 across all 57 panels. That zero was then read as a pipeline
failure: it triggered a legibility hypothesis, a VISION_MAX_PX experiment and
two paid model runs, none of which could have moved a counter that only ever
returned zero.

The contract is pinned here so a producer/consumer key mismatch fails a test
instead of an invoice.
"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
sys.path.insert(0, os.path.dirname(HERE))

R = []


def check(name, ok):
    R.append((name, bool(ok)))


def main():
    cp = open(os.path.join(HERE, "claude_plus.py"), encoding="utf-8").read()
    check("describe_plus writes the transcription as ocr_text",
          '"ocr_text": ocr,' in cp)
    check("...and it is NOT written as a bare 'ocr' key",
          '"ocr": ocr,' not in cp)

    srv = open(os.path.join(HERE, "server.py"), encoding="utf-8").read()
    check("the benchmark endpoint reads ocr_text", '_ocr_of(d)' in srv)
    check("...accepting both spellings so neither producer reads as empty",
          'd.get("ocr_text") or d.get("ocr")' in srv)
    check("...and no bare-'ocr' read survives in the counter",
          'str(d.get("ocr") or "").strip()' not in srv)

    import bubbles as B
    check("the gate already handled both spellings (it was never broken)",
          B.should_detect({"ocr_text": "SOME TEXT"})[0])
    check("...and treats a truly empty confident panel as skippable",
          not B.should_detect({"ocr_text": "", "ocr_confidence": 0.9})[0])
    check("...while an unsure reader still gets the geometry pass",
          B.should_detect({"ocr_text": "", "ocr_confidence": 0.1})[0])

    for name, ok in R:
        print(("PASS " if ok else "FAIL ") + name)
    n = sum(1 for _, ok in R if ok)
    print("\n%d/%d passed" % (n, len(R)))
    return 0 if n == len(R) else 1


if __name__ == "__main__":
    sys.exit(main())
