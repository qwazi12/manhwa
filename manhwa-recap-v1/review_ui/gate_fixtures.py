"""Test fixtures: chapters and compilations whose evidence is fully tied to
their files, the way the studio writes it (critique -> spoken lines, render
manifest -> export bytes, QA report -> manifest + bytes). Tests then break one
link at a time. Not imported by the app."""
import hashlib
import json
import os

import media_qa
import release_gate as rg

HALLU = {"unit": 3, "type": "hallucination", "problem": "invents a name", "fix": "remove it"}
CLEAN = {"status": "done", "outcome": "clean", "issues": [HALLU], "revised": [{"unit": 3}],
         "unresolved": [], "verified": {"units": [3], "still_flagged": []}}
OPEN = {"status": "done", "outcome": "needs_review", "issues": [HALLU], "revised": [{"unit": 3}],
        "unresolved": [{**HALLU, "resolution": "still_flagged_after_rewrite"}]}
UNVERIFIED = {"status": "done", "outcome": "needs_review", "issues": [HALLU], "revised": [{"unit": 3}],
              "unresolved": [{**HALLU, "resolution": "rewritten_unverified"}]}
FAILED_FINDINGS = [{"rule": "clipping", "severity": "critical", "explanation": "3% clipped"}]
LINES = ["He draws his sword.", "The gate opens.", "Nobody speaks."]


def w(path, value):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w") as f:
        json.dump(value, f)


def sha(path):
    with open(path, "rb") as f:
        return hashlib.sha256(f.read()).hexdigest()


def write_board(p, lines):
    w(os.path.join(p, "segments.json"), [
        {"seg_index": i, "user_included": True, "panel_id": f"p{i}",
         "beats": [{"index": i, "text": t, "start": float(i), "end": float(i + 1)}]}
        for i, t in enumerate(lines)])


def write_export(p, name, data, lines=None, qa="passed", findings=None):
    """The export file + the render manifest that describes it + a QA report
    about exactly those. lines=None -> a compilation (no narration events)."""
    path = os.path.join(p, "exports", name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "wb") as f:
        f.write(data)
    return path


def write_manifest(p, name, lines=None, components=None):
    digest = sha(os.path.join(p, "exports", name))
    if components is None:
        m = {"version": 1, "context": {"kind": "chapter", "speed": 1.0},
             "events": [{"beat": i, "text": t, "start": float(i), "end": float(i + 1)} for i, t in enumerate(lines)],
             "input_digest": "inputs-" + rg.narration_digest(lines)[:12], "export_digest": digest}
    else:
        m = {"version": 1, "context": {"kind": "range", "components": [
            {"project": cp, "name": cn} for cp, cn in components]}, "events": [],
            "inputs": {"kind": "range", "components": [
                {"project": cp, "name": cn, "export": rg._file_sha(os.path.join(cp, "exports", cn))}
                for cp, cn in components]},
            "input_digest": "range-inputs", "export_digest": digest}
    w(os.path.join(p, "exports", "_qa", name + ".manifest.json"), m)
    return m


def write_qa(p, name, status="passed", findings=None):
    m = json.load(open(os.path.join(p, "exports", "_qa", name + ".manifest.json")))
    w(os.path.join(p, "exports", "_qa", name + ".qa.json"),
      {"version": media_qa.VERSION, "status": status, "findings": findings or [],
       "export_digest": m["export_digest"], "input_digest": m["input_digest"],
       "manifest_digest": media_qa._digest(m)})


def chapter(root, pid, critique=CLEAN, qa="passed", lines=LINES, name="final.mp4", validation=None,
            data=None, bind=True):
    """A single chapter whose every check is tied to the export. critique=None
    -> no critique.json; qa=None -> no QA report; bind=False -> a critique
    from before spoken lines were recorded."""
    p = os.path.join(root, pid)
    w(os.path.join(p, "project.json"), {"id": pid})
    script = "\n".join(lines) + "\n"
    with open(os.path.join(p, "script.txt"), "w") as f:
        f.write(script)
    write_board(p, lines)
    if critique is not None:
        c = dict(critique)
        if bind:
            c.update(script_sha256=hashlib.sha256(script.encode()).hexdigest(),
                     narration_sha256=rg.narration_digest(lines))
        w(os.path.join(p, "critique.json"), c)
    if validation is not None:
        w(os.path.join(p, "validation.json"), validation)
    write_export(p, name, data if data is not None else f"video of {pid}".encode())
    write_manifest(p, name, lines)
    if qa is not None:
        write_qa(p, name, qa, FAILED_FINDINGS if qa == "failed" else None)
    return p


def merge(root, pid, pieces, name, store=True, qa="passed", data=None):
    """A compilation of pieces [(dir, export name)], built now."""
    p = os.path.join(root, pid)
    w(os.path.join(p, "project.json"), {"id": pid, "kind": "range", "build": {"status": "done", "export": name},
                                        "merged_from": [{"project": os.path.basename(cp), "name": cn} for cp, cn in pieces]})
    write_export(p, name, data if data is not None else f"compilation {pid}/{name}".encode())
    write_manifest(p, name, components=pieces)
    if qa is not None:
        write_qa(p, name, qa, FAILED_FINDINGS if qa == "failed" else None)
    if store:
        rg.store_evidence(p, name, pieces)
    return p
