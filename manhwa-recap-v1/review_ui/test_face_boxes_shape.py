"""A face answered as a bare box must not throw away the chapter's faces
(Murim Login 252, 2026-11-06: "'list' object has no attribute 'get'")."""
import json

from PIL import Image

import face_boxes as fb


def _post(rows):
    return lambda body: {"candidates": [{"content": {"parts": [{"text": json.dumps(rows)}]}}]}


def test_bare_box_faces_are_read_and_junk_is_skipped(tmp_path):
    f = tmp_path / "p.jpg"
    Image.new("RGB", (10, 10)).save(f)
    rows = [{"i": 0, "faces": [[100, 200, 500, 600], "junk", {"box_2d": [0, 0, 100, 100], "is_mc": True}]}]
    out = fb.detect(str(tmp_path), [("p", str(f))], api_key="k", _post=_post(rows))
    faces = out["p"]["faces"]
    assert len(faces) == 2
    assert faces[0]["box"] == [0.2, 0.1, 0.6, 0.5]          # converted from y-first 0..1000
