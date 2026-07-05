"""
Stage 1 entry point.

Usage:
    python main.py --images input/images --script input/script.txt \
                   --voice input/voice.mp3

Optional:
    --out build          output directory (default: build)
    --no-whisper         skip whisper; time beats proportionally

Produces in the output directory:
    beats.json     narration beats with start/end times
    shots.json     ordered image shots with timing
    timeline.json  combined view (the render manifest in embryo)
    output.mp4     the finished recap video
"""

import argparse
import json
import os
import sys

# Disable gRPC fork handlers to prevent deadlocks when spawning subprocesses (e.g. ffprobe/ffmpeg)
os.environ["GRPC_ENABLE_FORK_SUPPORT"] = "false"

import align
import render
import transcribe
import scraper
import tts
import beat_segmenter
import matcher


def load_env():
    # Read parent directory's .env file
    env_path = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".env"))
    if os.path.exists(env_path):
        with open(env_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if line and not line.startswith("#") and "=" in line:
                    k, v = line.split("=", 1)
                    os.environ[k.strip()] = v.strip()

IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".webp"}


def natural_key(name: str):
    import re
    return [int(t) if t.isdigit() else t.lower()
            for t in re.split(r"(\d+)", name)]


def load_images(folder: str):
    files = sorted(
        (f for f in os.listdir(folder)
         if os.path.splitext(f)[1].lower() in IMAGE_EXTS),
        key=natural_key,
    )
    if not files:
        sys.exit(f"No images found in {folder}")
    return [os.path.abspath(os.path.join(folder, f)) for f in files]


def main():
    load_env()
    ap = argparse.ArgumentParser(description="Manhwa recap Stage 1 prototype")
    ap.add_argument("--images", help="[LEGACY] folder of ordered panel images (not needed when using --descriptions)")
    ap.add_argument("--chapter-url", help="URL of the manhwa chapter to scrape panel images from")
    ap.add_argument("--script", required=True, help="narration script, one beat per line")
    ap.add_argument("--voice", required=True, help="recorded narration audio (mp3/wav/m4a)")
    ap.add_argument("--out", default="build", help="output directory")
    ap.add_argument("--no-whisper", action="store_true",
                    help="time beats proportionally instead of via whisper")
    ap.add_argument("--use-tts", action="store_true",
                    help="synthesize the voice track using Google Cloud TTS Chirp 3 HD")
    ap.add_argument("--limit-beats", type=int, help="limit processing to the first N beats (useful for quick testing)")
    ap.add_argument("--descriptions", help="Path to descriptions.json metadata")
    ap.add_argument("--panels-dir", help="Directory containing the panel PNG files (default: ../panel-split/review_crops)")
    ap.add_argument("--embed-model", default="all-MiniLM-L6-v2", help="embedding model for semantic matching")
    args = ap.parse_args()

    # --images is now optional; matcher uses descriptions.json for image selection
    if not args.images and not args.chapter_url and not args.descriptions:
        ap.error("Either --descriptions (recommended) or --images/--chapter-url must be provided")

    build_dir = os.path.abspath(args.out)
    os.makedirs(build_dir, exist_ok=True)

    # 1. inputs
    images = []
    if args.chapter_url:
        images_dir = os.path.join(build_dir, "scraped_images")
        scraper.download_chapter(args.chapter_url, images_dir)
        images = load_images(images_dir)
    elif args.images:
        images = load_images(args.images)

    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()
    voice = os.path.abspath(args.voice)
    print(f"[1/5] Loaded {len(images) if images else '(matcher-driven)'} images, script, voice track.")

    # 2. beats
    timed_automatically = False
    if args.use_tts:
        beats = beat_segmenter.segment_beats(script_text)
        print(f"[2/5] Segmented script into {len(beats)} sentence-based beats for TTS.")
    else:
        # Check if it has timestamps (e.g. from an existing SRT-style script)
        beats = align.parse_timed_script(script_text)
        if beats:
            print(f"[2/5] Parsed timestamped script into {len(beats)} beats.")
            timed_automatically = True
        else:
            beats = beat_segmenter.segment_beats(script_text)
            print(f"[2/5] Segmented script into {len(beats)} sentence-based beats.")

    if args.limit_beats:
        beats = beats[:args.limit_beats]
        # Re-index beats after slicing so indices are sequential
        for i, b in enumerate(beats):
            b["index"] = i
        print(f"      Limiting to first {args.limit_beats} beats.")

    # 2.5 voice track generation (if --use-tts is passed)
    if args.use_tts:
        print("[TTS] Starting Text-to-Speech generation...")
        tts_dir = os.path.join(build_dir, "tts")
        beats, timeline = tts.synth_all_beats(beats, tts_dir)
        print(f"[TTS] Mixing {len(timeline)} voice clips into {voice}...")
        tts.mix_voice_track(timeline, voice)
        timed_automatically = True

    # 3. timing
    if not timed_automatically:
        if args.no_whisper:
            dur = transcribe.audio_duration(voice)
            beats = align.time_beats_proportional(beats, dur)
            print(f"[3/5] Timed beats proportionally over {dur:.1f}s of audio.")
        else:
            print("[3/5] Transcribing with whisper (first run downloads the model)...")
            words = transcribe.transcribe_words(voice)
            beats = align.time_beats_with_words(beats, words)
            print(f"      Aligned {len(beats)} beats against {len(words)} spoken words.")
    else:
        if args.use_tts:
            print("[3/5] Using real durations from TTS audio clips (skipping Whisper/proportional timing).")
        else:
            print("[3/5] Using parsed timestamps from script (skipping Whisper/proportional timing).")

    # 4. shots (matching beats to panels)
    print("[4/5] Matching beats to panel descriptions...")
    desc_path = args.descriptions
    if not desc_path:
        # Check standard locations
        possible_paths = [
            os.path.abspath(os.path.join(build_dir, "descriptions.json")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "panel-describe", "descriptions.json")),
            os.path.abspath(os.path.join(os.path.dirname(__file__), "descriptions.json")),
        ]
        for p in possible_paths:
            if os.path.exists(p):
                desc_path = p
                break
        if not desc_path:
            sys.exit("Error: descriptions.json not found. Please provide --descriptions.")
            
    print(f"      Loading panel metadata from {desc_path}")
    desc_dir = os.path.dirname(os.path.abspath(desc_path))
    # Determine where the actual panel PNGs live
    if args.panels_dir:
        panels_dir = os.path.abspath(args.panels_dir)
    else:
        # Default: sibling panel-split/review_crops next to the project root
        panels_dir = os.path.abspath(
            os.path.join(os.path.dirname(__file__), "..", "panel-split", "review_crops")
        )
        if not os.path.isdir(panels_dir):
            panels_dir = desc_dir  # last-resort: same folder as descriptions.json
    with open(desc_path, encoding="utf-8") as f:
        panels = json.load(f)
    # Resolve relative file paths: try panels_dir first, fall back to desc_dir
    for p in panels:
        if p.get("file") and not os.path.isabs(p["file"]):
            candidate = os.path.join(panels_dir, p["file"])
            p["file"] = candidate if os.path.exists(candidate) else os.path.join(desc_dir, p["file"])
    # clean panels
    panels = [p for p in panels if p.get("ok", True) and p.get("width") and p.get("height")]
    if not panels:
        sys.exit("Error: No usable panels found in descriptions.json")

    # Match beats to panels
    assignments, method = matcher.match_beats_to_panels(beats, panels, args.embed_model)
    shots = matcher.build_timeline(beats, panels, assignments)
    print(f"      Matched {len(beats)} beats to {len(set(s['panel_id'] for s in shots))} distinct panels using {method}.")

    # data contracts
    def dump(name, obj):
        with open(os.path.join(build_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)

    dump("beats.json", beats)
    dump("beatsheet.json", shots)
    dump("timeline.json", {
        "video": {"width": 1920, "height": 1080, "fps": 30},
        "voice": voice,
        "beats": beats,
        "shots": shots,
    })

    # 5. render
    print("[5/5] Rendering (this is the slow part)...")
    out = render.render_video(shots, beats, voice, build_dir)
    print(f"\nDone: {out}")
    print("Artifacts: beats.json, beatsheet.json, timeline.json in the same folder.")


if __name__ == "__main__":
    main()
