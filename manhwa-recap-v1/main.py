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

import align
import render
import transcribe
import scraper

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
    ap = argparse.ArgumentParser(description="Manhwa recap Stage 1 prototype")
    ap.add_argument("--images", help="folder of ordered panel images")
    ap.add_argument("--chapter-url", help="URL of the manhwa chapter to scrape panel images from")
    ap.add_argument("--script", required=True, help="narration script, one beat per line")
    ap.add_argument("--voice", required=True, help="recorded narration audio (mp3/wav/m4a)")
    ap.add_argument("--out", default="build", help="output directory")
    ap.add_argument("--no-whisper", action="store_true",
                    help="time beats proportionally instead of via whisper")
    args = ap.parse_args()

    if not args.images and not args.chapter_url:
        ap.error("Either --images or --chapter-url must be provided")

    build_dir = os.path.abspath(args.out)
    os.makedirs(build_dir, exist_ok=True)

    # 1. inputs
    if args.chapter_url:
        images_dir = os.path.join(build_dir, "scraped_images")
        scraper.download_chapter(args.chapter_url, images_dir)
        images_folder = images_dir
    else:
        images_folder = args.images

    images = load_images(images_folder)
    with open(args.script, encoding="utf-8") as f:
        script_text = f.read()
    voice = os.path.abspath(args.voice)
    print(f"[1/5] Loaded {len(images)} images, script, voice track.")

    # 2. beats
    beats = align.split_beats(script_text)
    print(f"[2/5] Split script into {len(beats)} beats.")

    # 3. timing
    if args.no_whisper:
        dur = transcribe.audio_duration(voice)
        beats = align.time_beats_proportional(beats, dur)
        print(f"[3/5] Timed beats proportionally over {dur:.1f}s of audio.")
    else:
        print("[3/5] Transcribing with whisper (first run downloads the model)...")
        words = transcribe.transcribe_words(voice)
        beats = align.time_beats_with_words(beats, words)
        print(f"      Aligned {len(beats)} beats against {len(words)} spoken words.")

    # 4. shots
    shots = align.assign_shots(beats, images)
    print(f"[4/5] Assigned {len(images)} images into {len(shots)} shots.")

    # data contracts
    def dump(name, obj):
        with open(os.path.join(build_dir, name), "w", encoding="utf-8") as f:
            json.dump(obj, f, indent=2, ensure_ascii=False)

    dump("beats.json", beats)
    dump("shots.json", shots)
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
    print("Artifacts: beats.json, shots.json, timeline.json in the same folder.")


if __name__ == "__main__":
    main()
