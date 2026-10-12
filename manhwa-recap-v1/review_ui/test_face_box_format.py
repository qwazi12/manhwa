"""Face-check boxes (2026-10-11): asked for in Gemini's box_2d order, read
correctly, and a wrong box can never blur a large part of the cover."""
import face_boxes as fb
import thumbnail_studio as t


def test_box_2d_is_converted_to_x_first_fractions():
    assert fb._box_from({"box_2d": [166, 339, 371, 655]}, "box_2d", "box") == [0.339, 0.166, 0.655, 0.371]
    assert fb._box_from({"box": [0.1, 0.2, 0.3, 0.4]}, "box_2d", "box") == [0.1, 0.2, 0.3, 0.4]   # old answers
    assert fb._boxes_2d({"text_blocks_2d": [[575, 34, 947, 949]]}, "text_blocks_2d", "text_blocks") == \
        [[0.034, 0.575, 0.949, 0.947]]


def test_detect_parses_the_new_format_and_marks_it():
    import json
    reply = [{"i": 0, "faces": [{"box_2d": [100, 200, 300, 400], "name": "A", "is_mc": True}],
              "bubbles_2d": [], "text_blocks_2d": [[600, 30, 950, 950]], "bubble_coverage": 0, "watermark": False}]
    post = lambda body: {"candidates": [{"content": {"parts": [{"text": json.dumps(reply)}]}}]}
    import tempfile, os
    from PIL import Image
    d = tempfile.mkdtemp()
    f = os.path.join(d, "c.png")
    Image.new("RGB", (100, 140)).save(f)
    rec = fb.detect(d, [("_cover", f)], _post=post)["_cover"]
    assert rec["faces"][0]["box"] == [0.2, 0.1, 0.4, 0.3]
    assert rec["text_blocks"] == [[0.03, 0.6, 0.95, 0.95]] and rec["fmt"] == 2
    assert not fb._suspect(rec)


def test_old_swapped_cover_is_rechecked_once():
    swapped = {"text_blocks": [[0.014, 0.018, 0.045, 0.245], [0.575, 0.034, 0.947, 0.949]]}
    assert fb._suspect(swapped)
    assert not fb._suspect(dict(swapped, fmt=2))
    assert not fb._suspect({"text_blocks": [[0.03, 0.6, 0.95, 0.95]]})          # a normal logo band


def test_a_big_narrow_block_is_never_inpainted():
    _t, _b, marks = t.title_band_from_blocks([[0.014, 0.018, 0.045, 0.245], [0.575, 0.034, 0.947, 0.949]])
    assert marks == [[0.014, 0.018, 0.045, 0.245]]          # the small watermark only
    _t, bottom, marks = t.title_band_from_blocks([[0.018, 0.014, 0.245, 0.045], [0.034, 0.575, 0.949, 0.947]])
    assert bottom > 0.4 and marks == [[0.018, 0.014, 0.245, 0.045]]     # the real logo is trimmed
