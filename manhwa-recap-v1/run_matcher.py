"""
Run the matcher: align narration beats to described panels, emit a gap-free
beatsheet.

Usage:
    python run_matcher.py --beats beats.json \
                          --descriptions descriptions.json \
                          --out beatsheet.json

Optional:
    --embed-model all-MiniLM-L6-v2
        Use sentence-transformers embeddings for semantic matching (best
        quality). If sentence-transformers isn't installed, the tool falls
        back to lexical matching automatically and tells you which it used.

Inputs:
    beats.json         list of {index, text, start, end, ...} from the TTS
                       stage (start/end are REAL audio times).
    descriptions.json  from the panel-describe tool: per-panel
                       {panel_id, file, width, height, ocr_text,
                        visual_description, ok}.

Output:
    beatsheet.json     one shot per beat, gap-free, ready for the renderer.
"""

import argparse
import matcher


def main():
    ap = argparse.ArgumentParser(description="Match beats to panels, build gap-free timeline")
    ap.add_argument("--beats", required=True)
    ap.add_argument("--descriptions", required=True)
    ap.add_argument("--out", default="beatsheet.json")
    ap.add_argument("--embed-model", default=None,
                    help="sentence-transformers model name for semantic matching "
                         "(e.g. all-MiniLM-L6-v2). Omit for lexical matching.")
    args = ap.parse_args()

    matcher.run(args.beats, args.descriptions, args.out, args.embed_model)
    print("\nReview beatsheet.json: check that panel_id changes line up with "
          "where the narration moves to a new moment. Spot-check a few beats "
          "against their beat_text before rendering.")


if __name__ == "__main__":
    main()
    