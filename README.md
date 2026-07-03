# Manhwa Recap — Stage 1 Prototype

One job: prove that beat-driven alignment of static manhwa art produces a
watchable recap video. Nothing else. No scraper, no database, no API, no UI.

## What it does

Takes three inputs you provide by hand:

1. A folder of **pre-sliced panel images**, named in story order
   (`001.png`, `002.png`, ...).
2. A **narration script**, plain text, **one beat per line**.
3. A **voice track** — you reading that script (mp3, wav, or m4a).

And produces one MP4: panels shown in chronological order, timed to your
narration via whisper word timestamps, with Ken Burns zoom on normal panels,
vertical pan on tall webtoon slices, burned subtitles, and your voice muxed in.

It also emits `beats.json`, `shots.json`, and `timeline.json` — the embryonic
data contracts for Stage 1.5.

## Setup

Requires Python 3.10+ and ffmpeg on your PATH.

```bash
# macOS
brew install ffmpeg

python -m venv venv
source venv/bin/activate
pip install -r requirements.txt
```

First whisper run downloads the `base` model (~150 MB). Change model size in
`config.py` if you want faster (`tiny`) or more accurate (`small`).

## Run

```bash
python main.py \
  --images input/images \
  --script input/script.txt \
  --voice input/voice.mp3
```

Output lands in `build/output.mp4`. If whisper gives you trouble, add
`--no-whisper` to time beats proportionally by word count instead.

## How the alignment works (the thing being tested)

- Each script line is a **beat**.
- Whisper transcribes your voice with word timestamps; beats are mapped onto
  those words by cumulative word-count proportion. This assumes you read the
  script roughly verbatim — which you do, because you wrote it.
- Images are distributed across beats **chronologically**, weighted by beat
  duration (largest-remainder apportionment). More images than beats: long
  beats get multiple shots. Fewer images than beats: consecutive beats share
  a shot.
- Shots under 1.5 s are merged into a neighbor so nothing flickers.

## Judging the result (the actual point of Stage 1)

Watch `build/output.mp4` and ask:

1. Do the visuals land on the right narration moments most of the time?
2. Does the motion (zoom/pan) make static art feel alive or feel cheap?
3. Would you keep watching for 8 minutes of this?

If yes → the core assumption holds; move to Stage 1.5 (hardening the JSON
contracts) then Stage 2. If no → the fix is in `align.py` and `render.py`,
not in more infrastructure.

## Tuning knobs

Everything adjustable is in `config.py`: resolution, zoom speed, tall-panel
threshold, minimum shot length, subtitle style, whisper model.

## File map

| File | Role |
|---|---|
| `main.py` | CLI entry, orchestrates the five steps, writes JSON artifacts |
| `align.py` | Beat splitting + beat-to-shot alignment (the core mechanic) |
| `transcribe.py` | Whisper word timestamps + ffprobe duration fallback |
| `render.py` | FFmpeg motion clips, concat, subtitles, audio mux |
| `config.py` | All tunable constants |

## Known Stage 1 limits (deliberate)

- Alignment is proportional, not fuzzy-matched — fine if you read your own
  script; will drift if you ad-lib heavily.
- No panel cropping or composition intelligence — images are used whole.
- No retries, no state tracking, no review UI. That is Stage 2, and it only
  gets built if this video looks good.
