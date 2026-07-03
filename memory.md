# 🧠 Project Memory — Manhwa Recap System

> **What is this?** A universal, passive tracker for this project. Every significant
> input, output, decision, fix, and milestone is logged here chronologically.
> Any IDE, AI agent, or human collaborator should read this file first to understand
> what has been done, what broke, what was fixed, and what's next.
>
> **Rules:** Append only. Never delete history. Newest entries at the bottom.

---

## Project Overview

| Field | Value |
|---|---|
| **Project** | Manhwa Recap — Automated manhwa/webtoon recap video pipeline |
| **Repo** | `https://github.com/qwazi12/manhwa.git` |
| **Stage** | Stage 1 — Prototype (beat-driven alignment of static manhwa art) |
| **Stack** | Python 3.10+, FFmpeg, faster-whisper, Pillow |
| **Entry Point** | `python main.py --images <dir> --script <txt> --voice <audio>` |
| **Output** | `build/output.mp4` + JSON artifacts (`beats.json`, `shots.json`, `timeline.json`) |

---

## File Map

| File | Role |
|---|---|
| `main.py` | CLI entry, orchestrates the 5-step pipeline, writes JSON artifacts |
| `align.py` | Beat splitting + beat-to-shot alignment (the core mechanic under test) |
| `transcribe.py` | Whisper word timestamps + ffprobe duration fallback |
| `render.py` | FFmpeg motion clips, concat, subtitles, audio mux |
| `config.py` | All tunable constants (resolution, zoom, timing, whisper model, etc.) |
| `memory.md` | This file — universal project memory and decision log |
| `.gitignore` | Git exclusions (venv, build, .env, OS junk) |

---

## Session Log

### Session 1 — 2026-07-03 — Initial Setup & First Run

#### Environment Check
- **When:** 2026-07-03 12:32 ET
- **Python:** 3.13.2 ✅ (required: 3.10+)
- **FFmpeg:** 8.1 via Homebrew ✅ (minimal build — see known issues)
- **OS:** macOS (Apple Silicon / arm64)

#### Virtual Environment & Dependencies
- **When:** 2026-07-03 12:33 ET
- **Action:** Created `venv/`, installed `requirements.txt`
- **Packages:** faster-whisper 1.2.1, Pillow 12.3.0, ctranslate2 4.8.1, plus transitive deps
- **Result:** ✅ All installed successfully

#### First Pipeline Run (with Whisper)
- **When:** 2026-07-03 12:34 ET
- **Command:** `python main.py --images input/images --script input/script.txt --voice input/voice.mp3`
- **Result:** ❌ Failed at step 3
- **Error:** `ValueError: Whisper returned no words. Check the audio file.`
- **Root Cause:** Sample `voice.mp3` is a 32kbps placeholder (24s, ~96KB) with no actual speech. Whisper transcribes silence → 0 words → pipeline crashes.
- **Fix:** Used `--no-whisper` flag to time beats proportionally. This is expected behavior — the flag exists for exactly this case. Real voice recordings will work with Whisper.

#### Second Pipeline Run (no-whisper, subtitle crash)
- **When:** 2026-07-03 12:35 ET
- **Command:** `python main.py --images input/images --script input/script.txt --voice input/voice.mp3 --no-whisper`
- **Result:** ❌ Failed at step 5 (render — subtitle burn)
- **Error:** `No option name near 'subs.srt:force_style=...'` → FFmpeg filter graph parsing failure
- **Root Cause:** The installed FFmpeg build (`brew install ffmpeg`) lacks `--enable-libass`. The `subtitles` filter is not compiled in. Further investigation showed `drawtext` (needs `--enable-libfreetype`) is also missing.
- **Decision:** Instead of requiring the user to reinstall ffmpeg (system-level change), modified `render.py` to auto-detect available filters and gracefully degrade.

#### Code Change — render.py: Subtitle filter auto-detection
- **When:** 2026-07-03 12:37 ET
- **What changed:**
  - Added `_has_filter(name)` helper — probes `ffmpeg -filters` output at runtime
  - Final render step now has 3 tiers:
    1. `subtitles` filter (libass) — preferred, used if available
    2. `drawtext` filter (libfreetype) — fallback
    3. Audio-only mux — last resort, prints warning, writes `subs.srt` as sidecar
- **Files modified:** `render.py` (lines 27–33: new helper; lines 120–160: tiered render)
- **Behavior change:** Pipeline no longer crashes on systems without libass/libfreetype. Prints clear warning with fix instructions.

#### Third Pipeline Run — SUCCESS ✅
- **When:** 2026-07-03 12:37 ET
- **Command:** `python main.py --images input/images --script input/script.txt --voice input/voice.mp3 --no-whisper`
- **Result:** ✅ Full pipeline completed
- **Output:** `build/output.mp4` — 1920×1080, 30fps, H.264+AAC, 24s, ~3MB
- **Artifacts:** `beats.json`, `shots.json`, `timeline.json`, `subs.srt`
- **Note:** Subtitles not burned in (ffmpeg limitation) — `subs.srt` available as sidecar

#### GitHub Setup
- **When:** 2026-07-03 12:41 ET
- **Action:** Initialized git repo, created `.gitignore`, created `memory.md`
- **Remote:** `https://github.com/qwazi12/manhwa.git`
- **First commit:** Stage 1 prototype — working pipeline + fixes

---

## Known Issues & Limitations

| ID | Status | Issue | Impact | Fix |
|---|---|---|---|---|
| K-001 | ⚠️ OPEN | FFmpeg missing libass/libfreetype | No burned-in subtitles | `brew reinstall ffmpeg` |
| K-002 | ℹ️ BY DESIGN | Sample voice.mp3 has no speech | Whisper returns 0 words | Use `--no-whisper` for sample; real voice works fine |
| K-003 | ℹ️ BY DESIGN | Alignment is proportional, not fuzzy-matched | Fine if you read your own script; drifts with ad-lib | Stage 1 assumption |
| K-004 | ℹ️ BY DESIGN | No panel cropping or composition intelligence | Images used whole | Deliberate Stage 1 limit |

---

## Decisions Log

| # | Date | Decision | Rationale |
|---|---|---|---|
| D-001 | 2026-07-03 | Auto-detect ffmpeg filters instead of crashing | User shouldn't need to reinstall system software to test prototype |
| D-002 | 2026-07-03 | Keep `subs.srt` as sidecar even when burning fails | Preserves data contract; any player can load it |
| D-003 | 2026-07-03 | Do NOT add features or refactor toward Stage 2 | User's explicit instruction: Stage 1 clean run only |
| D-004 | 2026-07-03 | Accept no-subtitles-burn for Stage 1 | Homebrew's base `ffmpeg` formula deliberately excludes libass/libfreetype; building from source or a custom tap is out of scope for Stage 1 |

---

#### FFmpeg Reinstall Attempt
- **When:** 2026-07-03 12:44 ET
- **Action:** `brew update` then `brew reinstall ffmpeg`
- **Result:** FFmpeg upgraded from 8.1 → 8.1.2, but the base homebrew formula **does not include libass or libfreetype**
- **Investigation:** `brew info ffmpeg` shows only 10 required deps (dav1d, lame, libvmaf, libvpx, openssl, opus, sdl2, svt-av1, x264, x265). The caveats note mentions "ffmpeg-full" but that was a third-party tap concept, not part of core homebrew.
- **Filter check:** `ffmpeg -filters | grep subtitle` → still empty. `drawtext` also absent.
- **Impact:** Subtitle burning remains unavailable via the standard homebrew bottle. The pipeline's 3-tier auto-detection (subtitles → drawtext → audio-only) handles this gracefully.
- **Decision (D-004):** Accept this for Stage 1. Subtitles are written as `subs.srt` sidecar — loadable in any media player. Burned-in subtitles would require building ffmpeg from source with `--enable-libass --enable-libfreetype`, which is out of scope for prototype validation.

#### Pipeline Re-verification (post-upgrade)
- **When:** 2026-07-03 12:45 ET
- **Command:** `python main.py --images input/images --script input/script.txt --voice input/voice.mp3 --no-whisper`
- **Result:** ✅ Full pipeline completed with ffmpeg 8.1.2
- **Output:** `build/output.mp4` — 1920×1080, 30fps, H.264+AAC, 24s

#### GitHub Push — Initial Commit
- **When:** 2026-07-03 12:42 ET
- **Commit:** `d35e52c` — "Stage 1 prototype — working pipeline + ffmpeg subtitle fallback"
- **Files:** 17 files, 782 insertions
- **Remote:** `https://github.com/qwazi12/manhwa.git` (branch: `main`)

---

| K-001 | ✅ RESOLVED | FFmpeg (homebrew base) missing libass/libfreetype | None | Fixed by patching formula locally and running with HOMEBREW_NO_INSTALL_FROM_API=1 |
| K-002 | ℹ️ BY DESIGN | Sample voice.mp3 has no speech | Whisper returns 0 words | Use `--no-whisper` for sample; real voice works fine |
| K-003 | ℹ️ BY DESIGN | Alignment is proportional, not fuzzy-matched | Fine if you read your own script; drifts with ad-lib | Stage 1 assumption |
| K-004 | ℹ️ BY DESIGN | No panel cropping or composition intelligence | Images used whole | Deliberate Stage 1 limit |

---

#### FFmpeg Local Formula Patch & Subtitle Verification
- **When:** 2026-07-03 13:20 ET
- **Action:** Checked tap git status. Discovered Homebrew was installing from cached API.
- **Fix:** Ran `HOMEBREW_NO_INSTALL_FROM_API=1 brew reinstall --build-from-source ffmpeg`.
- **Result:** ffmpeg rebuilt successfully in 70s. Filter check confirmed `subtitles` and `ass` filters are now compiled in.
- **Verification:** Ran `python main.py --images input/images --script input/script.txt --voice input/voice.mp3 --no-whisper`.
- **Output:** Checked frame at 5 seconds. Subtitles are now successfully burned into the video.
- **Status:** K-001 resolved.

---

## What's Next (Pending User Input)

- [ ] Implement chapter URL scraper (Playwright/Scraper integration)
- [ ] User will bring real chapter art (sliced panel images)
- [ ] User will bring real narration script
- [ ] User will bring real recorded voice
- [ ] User will judge output quality before any changes are made

---

*Last updated: 2026-07-03 13:21 ET*

