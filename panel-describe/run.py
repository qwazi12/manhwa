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
import generation_cache as cache

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
    ap.add_argument("--series-bible", default="",
                    help="path to series_bible.json or series slug")
    ap.add_argument("--merge", action="store_true", 
                    help="merge new descriptions into existing output file (keeps unchanged panels)")
    args = ap.parse_args()

    bible_data = None
    if args.series_bible:
        if os.path.isfile(args.series_bible):
            try:
                with open(args.series_bible, encoding="utf-8") as f:
                    bible_data = json.load(f)
            except Exception as e:
                print(f"Warning: could not load series bible from {args.series_bible}: {e}")
        else:
            # Try loading as series slug
            _RECAP = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "manhwa-recap-v1"))
            if _RECAP not in sys.path:
                sys.path.insert(0, _RECAP)
            try:
                import series_bible as _sb
                bible_data = _sb.load_series_bible(args.series_bible)
            except Exception as e:
                print(f"Warning: could not load series bible for slug {args.series_bible}: {e}")

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
            states = {f: describe.cache_state(existing[os.path.splitext(f)[0]],
                       os.path.join(args.input, f), api_key, args.model, bible_data)
                      for f in files if os.path.splitext(f)[0] in existing}
            # Decide for the whole chapter BEFORE submitting any requests.
            # Legacy/changed evidence must never silently spend on refresh.
            blocked = {f: state for f, state in states.items() if state != "current"}
            cache.write_json(args.out + ".cache.json", {
                "status": "needs_review" if blocked else "current", "panels": states})
            if blocked:
                sys.exit("Description cache is stale or unknown; existing work preserved. "
                         "Review descriptions.json.cache.json, then explicitly rerun describe "
                         "(or --force-rerun) to refresh; normal generation costs apply.")
            files = [f for f in files if not (
                states.get(f) == "current" and existing[os.path.splitext(f)[0]].get("ok", False))]
            print(f"Merge mode: {len(files)} panels need (re)description, skipping the rest.\n")

    if os.path.exists(args.out) and (args.force_rerun or not args.merge):
        # Explicit standalone replacement is reviewable and recoverable, too.
        import shutil
        import uuid
        backup = args.out + ".previous-" + uuid.uuid4().hex + ".json"
        shutil.copy2(args.out, backup)

    print(f"Describing {len(files)} panels "
          f"({'Gemini: ' + args.model if api_key else 'Tesseract OCR-only'})...\n")

    def _one(fname):
        path = os.path.join(args.input, fname)
        before = describe.generation_inputs(path, api_key, args.model, bible_data)
        def stamped(rec):
            after = describe.generation_inputs(path, api_key, args.model, bible_data)
            rec["generation"] = {"version": cache.VERSION, "inputs": before}
            if before != after:
                rec.update(ok=False, error="Source/generation inputs changed during description; retry explicitly")
            return rec
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
            return stamped({"panel_id": os.path.splitext(fname)[0], "file": fname,
                    "width": _w, "height": _h, "bbox": [0, 0, _w, _h],
                    "ocr_text": "", "visual_description": "",
                    "source": "size-filter", "ok": True,
                    "_note": f"skipped (sliver {_w}x{_h})"})
        return stamped(describe.describe_panel(path, api_key, args.model, series_bible=bible_data))

    # Panels are independent, so several are described at once. Each call is
    # still gated by usage.gate (cross-process lock), so caps hold exactly.
    # DESCRIBE_WORKERS=1 restores the old one-at-a-time behaviour.
    from concurrent.futures import ThreadPoolExecutor
    workers = max(1, int(os.environ.get("DESCRIBE_WORKERS", "6"))) if api_key else 1
    by_name, done_n, cap_err = {}, 0, None
    with ThreadPoolExecutor(max_workers=workers) as ex:
        futs = {ex.submit(_one, f): f for f in files}
        from concurrent.futures import as_completed
        for fut in as_completed(futs):
            fname = futs[fut]
            if fut.cancelled():      # dropped after a usage cap tripped
                continue
            try:
                rec = fut.result()
            except Exception as e:
                if UsageCapExceeded and isinstance(e, UsageCapExceeded):
                    cap_err = cap_err or e
                    for other in futs:
                        other.cancel()
                    continue
                # A corrupt/unreadable panel must not discard other completed
                # paid descriptions. Persist an explicit failure and stop the
                # stage after the batch is saved, before narration can run.
                rec = {"panel_id": os.path.splitext(fname)[0], "file": fname,
                       "width": 0, "height": 0, "bbox": [0, 0, 0, 0],
                       "ocr_text": "", "visual_description": "", "lines": [],
                       "source": "none", "ok": False,
                       "error": f"{type(e).__name__}: {e}"}
            by_name[fname] = rec
            done_n += 1
            if rec.pop("_note", None):
                status = "skipped"
            else:
                status = "ok" if rec["ok"] else f"FAILED ({rec.get('error','')[:60]})"
            ocr_preview = (rec["ocr_text"][:40] + "…") if len(rec["ocr_text"]) > 40 else rec["ocr_text"]
            print(f"[{done_n:>3}/{len(files)}] {fname:32} {status:8} text: {ocr_preview!r}",
                  flush=True)
    records = [by_name[f] for f in files if f in by_name]   # original order
    # If merging, overlay new records on top of existing ones
    # CRITICAL: only replace existing record with new one if new run succeeded
    if args.merge and existing:
        merged = dict(existing)
        for rec in records:
            pid = rec["panel_id"]
            if rec.get("ok", False):
                # New description succeeded — update
                merged[pid] = rec
            elif (pid not in merged or not merged[pid].get("ok", False)
                  or describe.cache_state(merged[pid], os.path.join(args.input, rec["file"]),
                                          api_key, args.model, bible_data) != "current"):
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

    cache.write_json(args.out, final)
    states = {}
    for rec in final:
        state = "failed"
        if rec.get("ok"):
            try:
                state = describe.cache_state(rec, os.path.join(args.input, rec["file"]),
                                             api_key, args.model, bible_data)
            except OSError:
                state = "unknown"
        states[rec["panel_id"]] = state
    incomplete = bool(cap_err) or any(state != "current" for state in states.values())
    cache.write_json(args.out + ".cache.json", {
        "status": "incomplete" if incomplete else "current", "panels": states})
    if cap_err is not None:
        sys.exit(f"\nUSAGE CAP EXCEEDED — stopping: {cap_err}")

    ok = sum(1 for r in final if r["ok"])
    print(f"\nWrote {args.out}: {ok}/{len(final)} panels described successfully.")
    if ok < len(final):
        print("Some panels failed — check their 'error' field in the JSON.")
    if incomplete:
        sys.exit("Description pass incomplete — successful progress preserved; "
                 "review failed/stale panels before narration or an explicit retry.")
    print("\nReview the visual_description and ocr_text fields before we build the")
    print("matcher. If the descriptions are vague or wrong, that's the signal to")
    print("adjust the prompt or model — not to start matching yet.")


if __name__ == "__main__":
    main()
