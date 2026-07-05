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
    ap.add_argument("--images", help="folder of ordered panel images")
    ap.add_argument("--chapter-url", help="URL of the manhwa chapter to scrape panel images from")
    ap.add_argument("--script", required=True, help="narration script, one beat per line")
    ap.add_argument("--voice", required=True, help="recorded narration audio (mp3/wav/m4a)")
    ap.add_argument("--out", default="build", help="output directory")
    ap.add_argument("--no-whisper", action="store_true",
                    help="time beats proportionally instead of via whisper")
    ap.add_argument("--use-tts", action="store_true",
                    help="synthesize the voice track using Google Cloud TTS Chirp 3 HD")
    ap.add_argument("--limit-beats", type=int, help="limit processing to the first N beats (useful for quick testing)")
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
