
import os, sys, unittest
sys.path.insert(0, "manhwa-recap-v1")
sys.path.insert(0, "manhwa-recap-v1/review_ui")
import gemini_tts
import server

class TestGeminiTTSIntegration(unittest.TestCase):
    def test_config_resolution(self):
        os.environ["GEMINI_API_KEY"] = "mock_key_test"
        cfg = gemini_tts.get_tts_engine_config()
        self.assertEqual(cfg["provider"], "gemini")
        self.assertEqual(cfg["model"], "gemini-3.8-flash-tts")
        self.assertEqual(cfg["voice"], "Charon")

    def test_fallback_when_no_gemini(self):
        del os.environ["GEMINI_API_KEY"]
        os.environ["TTS_API_KEY"] = "mock_chirp_key"
        cfg = gemini_tts.get_tts_engine_config()
        self.assertEqual(cfg["provider"], "chirp")
        self.assertEqual(cfg["model"], "chirp3-hd-charon")
        if "TTS_API_KEY" in os.environ:
            del os.environ["TTS_API_KEY"]

if __name__ == "__main__":
    unittest.main()
