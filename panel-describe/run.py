"""
Run the panel-description pass over a folder of clean panels.

Usage:
    export GEMINI_API_KEY=your_key
    python run.py --input path/to/panels --out descriptions.json

Options:
    --model gemini-2.5-flash    (default; also try gemini-2.5-flash-lite for cheapest)
    --limit N                   process only the first N panels (for a cheap test run)
    --no-ai                     force Tesseract OCR-only, ignore any API key

Output: descriptions.json — a list of per-panel records. Review these before
building any matching logic on top of them.
"""

import argparse
import json
import os
import sys
import time

import describe

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def natural_key(name):
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def main():
    ap = argparse.ArgumentParser(description="Describe manhwa panels (OCR + visual)")
    ap.add_argument("--input", required=True, help="folder of clean panel images")
    ap.add_argument("--out", default="descriptions.json", help="output JSON path")
    ap.add_argument("--model", default="gemini-2.5-flash",
                    help="Gemini model id (default gemini-2.5-flash)")
    ap.add_argument("--limit", type=int, default=0, help="only process first N panels")
    ap.add_argument("--no-ai", action="store_true",
                    help="force Tesseract OCR-only, ignore GEMINI_API_KEY")
    args = ap.parse_args()

    api_key = None if args.no_ai else os.environ.get("GEMINI_API_KEY")
    if not api_key and not args.no_ai:
        print("WARNING: no GEMINI_API_KEY found in environment.")
        print("Falling back to Tesseract OCR-only — visual_description will be empty.")
        print("Set GEMINI_API_KEY for the full description pass, or pass --no-ai to")
        print("silence this and run OCR-only intentionally.\n")

    files = sorted(
        (f for f in os.listdir(args.input)
         if os.path.splitext(f)[1].lower() in IMAGE_EXTS),
        key=natural_key,
    )
    if args.limit:
        files = files[:args.limit]
    if not files:
        sys.exit(f"No panel images found in {args.input}")

    print(f"Describing {len(files)} panels "
          f"({'Gemini: ' + args.model if api_key else 'Tesseract OCR-only'})...\n")

    records = []
    for i, fname in enumerate(files, 1):
        path = os.path.join(args.input, fname)
        rec = describe.describe_panel(path, api_key, args.model)
        records.append(rec)
        status = "ok" if rec["ok"] else f"FAILED ({rec.get('error','')[:60]})"
        ocr_preview = (rec["ocr_text"][:40] + "…") if len(rec["ocr_text"]) > 40 else rec["ocr_text"]
        print(f"[{i:>3}/{len(files)}] {fname:32} {status:8} text: {ocr_preview!r}")
        if api_key:
            time.sleep(0.5)  # gentle pacing for free-tier rate limits

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)

    ok = sum(1 for r in records if r["ok"])
    print(f"\nWrote {args.out}: {ok}/{len(records)} panels described successfully.")
    if ok < len(records):
        print("Some panels failed — check their 'error' field in the JSON.")
    print("\nReview the visual_description and ocr_text fields before we build the")
    print("matcher. If the descriptions are vague or wrong, that's the signal to")
    print("adjust the prompt or model — not to start matching yet.")


if __name__ == "__main__":
    main()
