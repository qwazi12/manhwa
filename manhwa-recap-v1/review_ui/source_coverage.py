"""Free source-to-script evidence linkage. This does NOT assess semantics.

A line offered as evidence is not necessarily represented in the narration.
Missing links are review candidates, not a verdict that the story omitted it.
"""
import json
from pathlib import Path
import re

import generation_cache as cache
import matcher
import ocr_lines

VERSION = 1


def plain(text):
    return " ".join(re.findall(r"\w+", text.casefold()))


def inspect(pdir):
    pdir = Path(pdir)
    identity = {name: cache.file_digest(pdir / name)
                for name in ("descriptions.json", "script.json", "script.txt")}
    out = {"version": VERSION, "identity": cache.digest(identity),
           "semantic_coverage": "not_assessed", "status": "incomplete", "findings": [],
           "lines": [], "events": []}
    try:
        panels = json.loads((pdir / "descriptions.json").read_text(encoding="utf-8"))
        scenes = json.loads((pdir / "script.json").read_text(encoding="utf-8"))
        script = (pdir / "script.txt").read_text(encoding="utf-8")
        if not isinstance(panels, list) or not isinstance(scenes, list) or not scenes or not script.strip():
            raise ValueError("Nonempty structured source and script are required")
        linked_lines, linked_panels = set(), set()
        for scene in scenes:
            if scene.get("silent") or not (scene.get("text") or "").strip():
                continue
            linked_panels.update(scene.get("panel_ids") or [])
            for line in scene.get("source_line_refs") or []:
                # A stale reference with changed source text is not a match.
                linked_lines.add((line.get("source_line_id"), plain(line.get("text") or "")))
        for panel in panels:
            if not panel.get("ok", True):
                continue
            if panel.get("role") != "bubble" and matcher.is_junk_panel(panel):
                continue
            pid = panel["panel_id"]
            for line in ocr_lines.source_lines(panel):
                if line["type"] == "sfx":
                    continue
                linked = (line["source_line_id"], plain(line["text"])) in linked_lines
                item = {**line, "evidence_link": "linked" if linked else "unlinked",
                        "verbatim_in_script": f" {plain(line['text'])} " in f" {plain(script)} "}
                out["lines"].append(item)
                if not linked:
                    out["findings"].append({"kind": "source_line_unlinked", "panel_id": pid,
                        "source_line_id": line["source_line_id"], "text": line["text"],
                        "reason": "No matching source-line reference in spoken script provenance; review for omission or a missing legacy link."})
            desc = (panel.get("visual_description") or "").strip()
            if desc and panel.get("role") != "bubble":
                linked = pid in linked_panels
                out["events"].append({"panel_id": pid, "text": desc,
                                      "evidence_link": "linked" if linked else "unlinked"})
                if not linked:
                    out["findings"].append({"kind": "source_event_unlinked", "panel_id": pid,
                        "text": desc, "reason": "Source artwork has no spoken script provenance link; review its story importance."})
        if not out["lines"] and not out["events"]:
            raise ValueError("No eligible source evidence was found")
        out["status"] = "needs_review" if out["findings"] else "evidence_linked"
        # Agreement of source links cannot certify an independently edited
        # plain script. Keep the uncertainty explicit and non-blocking.
        if plain(script) != plain(" ".join(s.get("text") or "" for s in scenes if not s.get("silent"))):
            out["status"] = "needs_review"
            out["findings"].append({"kind": "script_provenance_disagree", "panel_id": None,
                "text": "Plain script and structured narration differ.",
                "reason": "Review both versions before assessing coverage."})
    except (OSError, ValueError, KeyError, TypeError, AttributeError) as e:
        out["status"] = "incomplete"
        out["error"] = f"Source linkage check unavailable: {type(e).__name__}: {e}"
    return out
