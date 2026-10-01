"""Unit tests for the Series Bible architecture and its interconnected pipeline integrations:
1. series_bible loading, formatting for vision, narrator, and SEO
2. panel-describe integration
3. narrate.py integration
4. seo.py truth_card enrichment
5. shot_planner multi-beat dynamic variation (eliminating frozen stills)
"""

import json
import os
import sys
import tempfile
import unittest

HERE = os.path.dirname(os.path.abspath(__file__))
if HERE not in sys.path:
    sys.path.insert(0, HERE)
_REVIEW_UI = os.path.join(HERE, "review_ui")
if _REVIEW_UI not in sys.path:
    sys.path.insert(0, _REVIEW_UI)
_DESCRIBE = os.path.abspath(os.path.join(HERE, "..", "panel-describe"))
if _DESCRIBE not in sys.path:
    sys.path.insert(0, _DESCRIBE)

import series_bible
import narrate
import seo
import shot_planner


class TestSeriesBible(unittest.TestCase):

    def setUp(self):
        self.bible = series_bible.load_series_bible("murim-psychopath")

    def test_bible_loading(self):
        self.assertIsNotNone(self.bible)
        self.assertEqual(self.bible["canonical_title"], "Murim Psychopath")
        self.assertTrue(len(self.bible["characters"]) >= 2)
        protagonist = next((c for c in self.bible["characters"] if c["role"] == "protagonist"), None)
        self.assertIsNotNone(protagonist)
        self.assertEqual(protagonist["name"], "Yu Shin")
        self.assertEqual(protagonist["gender"], "male")

    def test_format_for_vision(self):
        text = series_bible.format_for_vision(self.bible)
        self.assertIn("KNOWN SERIES CONTEXT", text)
        self.assertIn("Murim Psychopath", text)
        self.assertIn("Yu Shin", text)
        self.assertIn("COSTUMES", text)
        self.assertIn("STAGE PROPS", text)

    def test_format_for_narrator(self):
        text = series_bible.format_for_narrator(self.bible)
        self.assertIn("SERIES BIBLE & CANONICAL CAST", text)
        self.assertIn("Yu Shin", text)
        self.assertIn("he/him", text)
        self.assertIn("Cursed Killing Star", text)

    def test_format_for_seo(self):
        data = series_bible.format_for_seo(self.bible)
        self.assertEqual(data["canonical_title"], "Murim Psychopath")
        self.assertIn("Yu Shin", data["characters"])
        self.assertIn("Cursed Killing Star", data["characters"])
        self.assertTrue(len(data["keywords"]) > 0)
        self.assertTrue(len(data["hashtags"]) > 0)

    def test_narrate_prompt_with_bible(self):
        panels = [{
            "panel_id": "test_001",
            "visual_description": "Warrior with dark hair tied back raises blade",
            "ocr_text": "Taste my blade!"
        }]
        prompt = narrate.build_prompt(panels, series_bible_data=self.bible)
        self.assertIn("SERIES BIBLE & CANONICAL CAST", prompt)
        self.assertIn("CANONICAL CAST & WORLD ACCURACY", prompt)
        self.assertIn("Yu Shin", prompt)

    def test_seo_truth_card_enrichment(self):
        td = tempfile.mkdtemp()
        meta = {
            "series": "Murim Psychopath",
            "chapter": "44",
            "url": "https://asurascans.com/comics/murim-psychopath-12345/chapter/44"
        }
        with open(os.path.join(td, "project.json"), "w") as f:
            json.dump(meta, f)
        series_bible.save_series_bible("murim-psychopath", self.bible, pdir=td)

        card = seo.truth_card(td)
        self.assertIn("Yu Shin", card["characters"])
        self.assertIn("Cursed Killing Star", card["characters"])
        self.assertIn("Crazy Demon", card["aliases"])

    def test_shot_planner_multi_beat_punchin(self):
        """Verifies that multiple shots on the same panel receive dynamic framing
        variation so they do not freeze on identical still crops for 20s+."""
        shots = [
            {"index": 0, "panel_id": "p001", "beat_text": "The wind howled.", "start": 0.0, "end": 6.0},
            {"index": 1, "panel_id": "p001", "beat_text": "The martial artist drew his sword.", "start": 6.0, "end": 13.0},
            {"index": 2, "panel_id": "p001", "beat_text": "Silence fell over the courtyard.", "start": 13.0, "end": 20.0},
        ]
        td = tempfile.mkdtemp()
        crops_dir = os.path.join(td, "crops")
        os.makedirs(crops_dir)
        from PIL import Image
        img = Image.new("RGB", (800, 600), color=(100, 100, 100))
        img.save(os.path.join(crops_dir, "p001.png"))

        planned = shot_planner.plan_shots(shots, os.path.join(td, "desc.json"), crops_dir)
        self.assertEqual(len(planned), 3)
        self.assertEqual(planned[0]["crop_bbox_norm"], [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(planned[1]["focus_source"], "multi_beat_punchin")
        self.assertNotEqual(planned[1]["crop_bbox_norm"], [0.0, 0.0, 1.0, 1.0])
        self.assertEqual(planned[2]["focus_source"], "multi_beat_punchin")
        self.assertNotEqual(planned[2]["crop_bbox_norm"], planned[1]["crop_bbox_norm"])


if __name__ == "__main__":
    unittest.main()
