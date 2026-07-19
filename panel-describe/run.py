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
UsageCapExceeded = describe.usage.UsageCapExceeded if describe.usage else None


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
            # Skip panels that already have a good description — but only if
            # the crop on disk is still the SAME crop that was described.
            # Filename alone is not identity: a splitter change (e.g. YOLO
            # weights appearing/disappearing) reuses names for different
            # content, and stale descriptions then poison narrate/match.
            # Recorded width/height vs the file's current dimensions is a
            # cheap, reliable staleness check.
            def _still_valid(f):
                rec = existing.get(os.path.splitext(f)[0])
                if not rec or not rec.get("ok", False):
                    return False
                try:
                    from PIL import Image
                    with Image.open(os.path.join(args.input, f)) as im:
                        return im.size == (rec.get("width"), rec.get("height"))
                except Exception:
                    return False
            files = [f for f in files if not _still_valid(f)]
            print(f"Merge mode: {len(files)} panels need (re)description, skipping the rest.\n")

    print(f"Describing {len(files)} panels "
          f"({'Gemini: ' + args.model if api_key else 'Tesseract OCR-only'})...\n")

    records = []
    for i, fname in enumerate(files, 1):
        path = os.path.join(args.input, fname)
        # Pre-describe junk gate: sliver crops (stray gutter lines, cut-off
        # SFX fragments) can never carry a narration beat — the matcher's
        # junk filter drops them by content anyway. Skipping them here saves
        # one Gemini call each (dungeon-odyssey ch1: dozens of such slivers).
        # Conservative floor: nothing story-bearing is this small on a
        # 712px-wide webtoon page.
        try:
            from PIL import Image
            with Image.open(path) as _im:
                _w, _h = _im.size
        except Exception:
            _w = _h = 0
        if _w and _h and (min(_w, _h) < 40 or _w * _h < 10000):
            rec = {"panel_id": os.path.splitext(fname)[0], "file": fname,
                   "width": _w, "height": _h, "bbox": [0, 0, _w, _h],
                   "ocr_text": "", "visual_description": "",
                   "source": "size-filter", "ok": True}
            records.append(rec)
            print(f"[{i:>3}/{len(files)}] {fname:32} skipped (sliver {_w}x{_h})")
            continue
        try:
            rec = describe.describe_panel(path, api_key, args.model)
        except UsageCapExceeded as e:
            # Save whatever we already have before exiting, so partial
            # progress isn't lost, then stop the run with a clear reason.
            if records:
                with open(args.out, "w", encoding="utf-8") as f:
                    json.dump(records, f, indent=2, ensure_ascii=False)
                print(f"\nSaved {len(records)} panels described before the cap tripped.")
            sys.exit(f"\nUSAGE CAP EXCEEDED — stopping: {e}")
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
        # Drop GHOST records: descriptions of crops that no longer exist on
        # disk (a splitter change removed/renamed them). Keeping them feeds
        # phantom panels into narrate/match downstream.
        on_disk = {os.path.splitext(f)[0]
                   for f in os.listdir(args.input)
                   if os.path.splitext(f)[1].lower() in IMAGE_EXTS}
        merged = {pid: rec for pid, rec in merged.items() if pid in on_disk}
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
