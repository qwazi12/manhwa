"""
Run the panel-description pass over a folder of clean panels.

Usage:
    export GEMINI_API_KEY=your_key
    python run.py --input path/to/panels --out descriptions.json

Options:
    --model gemini-3.5-flash    (default; also try gemini-3.1-flash-lite for cheapest)
    --limit N                   process only the first N panels (for a cheap test run)
    --no-ai                     force Tesseract OCR-only, ignore any API key
    --force-rerun               re-describe ALL panels even if out file already exists
    --merge                     merge new descriptions into existing out file (update in place)

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
    ap.add_argument("--model", default="gemini-3.5-flash",
                    help="Gemini model id (default gemini-3.5-flash)")
    ap.add_argument("--limit", type=int, default=0, help="only process first N panels")
    ap.add_argument("--no-ai", action="store_true",
                    help="force Tesseract OCR-only, ignore GEMINI_API_KEY")
    ap.add_argument("--force-rerun", action="store_true",
                    help="re-describe all panels, ignoring any existing output file")
    ap.add_argument("--merge", action="store_true",
                    help="merge new descriptions into existing output file (keeps unchanged panels)")
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

    # Load existing records if merging
    existing = {}
    if args.merge and os.path.exists(args.out):
        with open(args.out, encoding="utf-8") as f:
            for rec in json.load(f):
                existing[rec["panel_id"]] = rec
        if not args.force_rerun:
            # Skip panels that already have a good description
            files = [f for f in files
                     if os.path.splitext(f)[0] not in existing
                     or not existing[os.path.splitext(f)[0]].get("ok", False)]
            print(f"Merge mode: {len(files)} panels need (re)description, skipping the rest.\n")

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

    # If merging, overlay new records on top of existing ones
    # CRITICAL: only replace existing record with new one if new run succeeded
    if args.merge and existing:
        merged = dict(existing)
        for rec in records:
            pid = rec["panel_id"]
            if rec.get("ok", False):
                # New description succeeded — update
                merged[pid] = rec
            elif pid not in merged or not merged[pid].get("ok", False):
                # New and old both failed (or no existing) — store failure for visibility
                merged[pid] = rec
            # else: new failed but old was good — keep the old one (no-op)
        # Re-sort by panel_id (natural order)
        final = sorted(merged.values(), key=lambda r: natural_key(r["panel_id"]))
    else:
        final = records

    with open(args.out, "w", encoding="utf-8") as f:
        json.dump(final, f, indent=2, ensure_ascii=False)

    ok = sum(1 for r in final if r["ok"])
    print(f"\nWrote {args.out}: {ok}/{len(final)} panels described successfully.")
    if ok < len(final):
        print("Some panels failed — check their 'error' field in the JSON.")
    print("\nReview the visual_description and ocr_text fields before we build the")
    print("matcher. If the descriptions are vague or wrong, that's the signal to")
    print("adjust the prompt or model — not to start matching yet.")


if __name__ == "__main__":
    main()
