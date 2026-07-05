# Panel Description Pass

Reads what's in each manhwa panel — both the **dialogue text** (OCR) and a
**visual description** of what's happening — and writes it to JSON. This is
the "understand the image" stage that a later matcher will use to align
script beats to panels.

**This tool does NOT do any matching.** It only describes. Matching is a
separate, later stage, built only after you've reviewed these descriptions
and judged them good enough.

## What it outputs

`descriptions.json` — one record per panel:

```json
{
  "panel_id": "page004_panel_003",
  "file": "page004_panel_003.png",
  "width": 792,
  "height": 1376,
  "bbox": [0, 0, 792, 1376],
  "ocr_text": "YOU MUST'VE HAD QUITE A LOT OF TROUBLE RUNNING FOR AN HOUR TO REACH HERE. / WELL DONE, PRINCE CHEON.",
  "visual_description": "A hooded, masked figure stands on a tree branch at night, looking down. Wide shot, moonlit forest, ominous mood.",
  "source": "gemini",
  "ok": true
}
```

## Why this design (from research)

Modern vision-language models do OCR **and** description in a single API
call — the old "run OCR, then separately describe" two-step collapses into
one. Research findings that shaped this:

- **No separate OCR engine needed when using a vision model.** Gemini 2.5
  scored 95%+ on end-to-end structured extraction out of the box. One call
  returns both `ocr_text` and `visual_description` as JSON.
- **No Japanese manga-OCR stack needed.** These panels are already
  English-translated (bubbles read "PRINCE CHEON", "DAMN IT ALL"), so the
  specialized vertical-Japanese OCR tools (manga-ocr, koharu, etc.) don't
  apply. Standard vision handling is enough.
- **Gemini 2.5 Flash is the value pick.** Free tier ≈ 1,500 panels/day
  (plenty for a chapter). Paid it's about $0.001 per panel. `--model
  gemini-2.5-flash-lite` is even cheaper if you want to trade a little
  quality for cost.

Why the vision model matters, proven on your own panels: in an OCR-only
test, Tesseract read the clean speech-bubble panels fine but returned
**nothing** for the atmospheric and action panels (misty mountains, the
falling sequence, the hooded man) — no text to read, and OCR can't describe
a scene. Three of five panels were useless without a vision model. The
`visual_description` field is the whole reason this stage exists.

## Setup

```bash
python -m venv venv && source venv/bin/activate
pip install -r requirements.txt
export GEMINI_API_KEY=your_key_here     # get one free at aistudio.google.com
```

Optional (only for the OCR-only fallback): install the tesseract binary —
`brew install tesseract` (macOS) or `apt-get install tesseract-ocr` (Ubuntu).

## Run

```bash
python run.py --input path/to/clean_panels --out descriptions.json
```

Useful flags:
- `--limit 5` — process only the first 5 panels for a cheap test before
  committing to a whole chapter.
- `--model gemini-3.1-flash-lite` — cheapest Gemini option.
- `--no-ai` — force Tesseract OCR-only (no visual description). For testing
  the pipeline without a key; not the real use case.

## What to check before moving on

Open `descriptions.json` and read the `visual_description` fields. Ask:

1. Does each description actually capture who's in frame and what's
   happening — enough that you could match a script sentence to it?
2. Is the `ocr_text` catching the bubble dialogue accurately (names
   included)?
3. Are the atmospheric/action panels (the ones OCR can't handle) getting
   useful descriptions?

If yes → the descriptions are a solid base for the matcher. If they're
vague ("a person stands somewhere"), adjust `VISION_PROMPT` in
`describe.py` or try a stronger model before building matching logic on top.

## Known limits (intentional)

- One panel = one image = one description. No sub-panel breakdown; feed it
  already-clean panels from the splitter (with slivers already quarantined).
- `bbox` is the panel's own frame `[0,0,w,h]`. If you later carry
  page-level coordinates, this field is where they'd go.
- Rate-limited to 2 calls/sec by default (`time.sleep(0.5)`) to stay under
  free-tier limits; remove or lower it on a paid tier for speed.
