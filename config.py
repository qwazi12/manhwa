"""
Stage 1 configuration. Every tunable knob lives here.
Change values here, not inside the pipeline modules.
"""

# --- Output video ---
WIDTH = 1920
HEIGHT = 1080
FPS = 30

# --- Motion ---
# Images taller than this height/width ratio get a vertical pan
# (typical webtoon slice). Everything else gets a Ken Burns zoom.
TALL_RATIO = 1.4

# Zoom strength per frame (Ken Burns). 0.0009 at 30fps ≈ 1.0 -> 1.13x over 5s.
ZOOM_STEP = 0.0009
ZOOM_MAX = 1.18

# Oversized canvas used before zooming to avoid zoompan jitter.
ZOOM_CANVAS_W = 3840
ZOOM_CANVAS_H = 2160

# --- Timing ---
MIN_SHOT_SEC = 1.5      # shots shorter than this get merged into a neighbor
TAIL_PAD_SEC = 0.5      # freeze last shot briefly after narration ends

# --- Whisper ---
WHISPER_MODEL = "base"  # tiny / base / small / medium. base is a good start.
WHISPER_DEVICE = "cpu"  # set to "cuda" if you have a GPU
WHISPER_COMPUTE = "int8"

# --- Subtitles ---
SUBTITLE_STYLE = (
    "FontName=Arial,FontSize=16,Bold=1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,"
    "Outline=2,Shadow=0,MarginV=45"
)

# --- Encoding ---
VIDEO_CODEC = ["-c:v", "libx264", "-preset", "medium", "-crf", "18",
               "-pix_fmt", "yuv420p"]
AUDIO_CODEC = ["-c:a", "aac", "-b:a", "192k"]
