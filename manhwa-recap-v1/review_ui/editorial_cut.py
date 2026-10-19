"""Explicit, source-linked editorial cuts; no inference, network or file writes.

The operator supplies unique sentence prefixes and reviewed replacement text.
Unchanged audio is reused. Changed speech must be recorded before materializing
the cut. A new project identity is required by the operational caller.
"""
from copy import deepcopy
from pathlib import Path


def plan(segments, recipe):
    beats = {}
    for segment in segments:
        if not segment.get("user_included"):
            continue
        for beat in segment.get("beats", []):
            if beat["index"] in beats:
                raise ValueError("Sliced/repeated beats need explicit consolidation first")
            beats[beat["index"]] = (segment, beat)
    chosen = []
    seen = set()
    forbidden = set(recipe.get("forbidden_panels", []))
    aliases = recipe.get("aliases", {})
    for item in recipe["keep"]:
        matches = [(s, b) for s, b in beats.values()
                   if b.get("text", "").startswith(item["prefix"])]
        if len(matches) != 1:
            raise ValueError("Sentence prefix must match exactly once: " + item["prefix"])
        s, b = matches[0]
        if b["index"] in seen:
            raise ValueError("Repeated editorial selection")
        seen.add(b["index"])
        if s["panel_id"] in forbidden:
            raise ValueError("Decorative/non-story panel cannot enter the cut")
        text = item.get("text", b["text"])
        for before, after in aliases.items():
            text = text.replace(before, after)
        chosen.append({"segment": deepcopy(s), "beat": deepcopy(b),
                       "text": text, "changed": text != b["text"]})
    # Prefix order must preserve the source timeline, including explicit edits.
    original_order = {b["index"]: pos for pos, s in enumerate(segments)
                      for b in s.get("beats", [])}
    order = [original_order[x["beat"]["index"]] for x in chosen]
    if order != sorted(order):
        raise ValueError("A cut must preserve source order")
    if not chosen:
        raise ValueError("Empty editorial cut")
    return chosen


def materialize(chosen, *, source, destination, audio):
    """Audio maps original beat ids to {file, duration, text} verified by caller.

    Times are rebuilt from actual decoded source files; stale absolute offsets
    and planner boxes from another panel are never carried into the new cut.
    """
    source, destination = Path(source).resolve(), Path(destination).resolve()
    if source == destination:
        raise ValueError("Editorial revision requires a separate project")
    result, script, cursor = [], [], 0.0
    for index, item in enumerate(chosen):
        s, b = deepcopy(item["segment"]), deepcopy(item["beat"])
        a = audio[b["index"]]
        if a["text"] != item["text"]:
            raise ValueError("Speech content does not match the editorial text")
        file = Path(a["file"])
        if file.is_absolute() or ".." in file.parts:
            raise ValueError("Audio must stay inside the revision's audio directory")
        duration = float(a["duration"])
        if not 0.1 < duration < 120:
            raise ValueError("Invalid decoded audio duration")
        panel = Path(s["panel_file"]).resolve()
        try:
            relative = panel.relative_to(source)
        except ValueError:
            raise ValueError("Source panel escaped its project") from None
        s.update(seg_index=index, panel_file=str(destination / relative),
                 start=round(cursor, 3), dur=round(duration + .08, 3),
                 clip=f"clips/seg_{index:03d}.mp4", user_included=True)
        b.update(text=item["text"], file=str(file), start=s["start"],
                 end=round(cursor + duration, 3))
        # A whole recorded line is used; parent slice offsets are inapplicable.
        for key in ("audio_offset", "slice_start", "slice_end"):
            b.pop(key, None)
        s["beats"] = [b]
        s["end"] = round(cursor + s["dur"], 3)
        s["editorial_origin"] = {"segment": item["segment"]["seg_index"],
                                  "beat": b["index"], "changed": item["changed"]}
        result.append(s)
        script.append({"scene_id": index, "panel_ids": [s["panel_id"]],
                       "text": b["text"], "silent": False,
                       "source_line_refs": deepcopy(b.get("source_line_refs", [])),
                       "editorial_origin": s["editorial_origin"]})
        cursor = s["end"]
    return result, script
