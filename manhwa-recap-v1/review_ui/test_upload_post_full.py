
import os, sys, unittest
sys.path.insert(0, "manhwa-recap-v1/review_ui")
import server
import upload_post

class TestUploadPostPublishing(unittest.TestCase):
    def setUp(self):
        os.environ["UPLOAD_POST_API_KEY"] = "mock_key_999"
        os.environ["UPLOAD_POST_PROFILE"] = "studio_prod"

    def tearDown(self):
        if "UPLOAD_POST_API_KEY" in os.environ:
            del os.environ["UPLOAD_POST_API_KEY"]
        if "UPLOAD_POST_PROFILE" in os.environ:
            del os.environ["UPLOAD_POST_PROFILE"]

    def test_provider_selection(self):
        name, mod = server._get_publish_backend()
        self.assertEqual(name, "upload_post")
        self.assertEqual(mod.__name__, "upload_post")

    def test_status_shape(self):
        root = "/tmp"
        status = upload_post.accounts_status(root)
        self.assertTrue(status["configured"])
        self.assertIn("youtube", status["networks"])
        self.assertIn("tiktok", status["networks"])
        self.assertIn("instagram", status["networks"])

    def test_direct_video_upload_mock(self):
        dummy_video = "/tmp/test_upload_video.mp4"
        with open(dummy_video, "wb") as f:
            f.write(b"mock_mp4_bytes" * 50)

        calls = []
        def mock_http(method, url, body, cfg):
            calls.append((method, url, len(body)))
            return {"success": True, "post_id": "up_post_12345"}

        res = upload_post.upload_video_post(
            dummy_video,
            {
                "title": "Murim Psychopath Ch.44 Recap",
                "description": "Full chapter recap with story bible sync",
                "privacy": "private",
                "tags": ["manhwa", "murim"],
                "made_for_kids": False,
            },
            ["youtube", "tiktok", "instagram"],
            _http=mock_http
        )
        self.assertTrue(res["success"])
        self.assertEqual(res["post_id"], "up_post_12345")
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0][0], "POST")
        self.assertIn("/api/upload", calls[0][1])

        if os.path.exists(dummy_video):
            os.remove(dummy_video)

if __name__ == "__main__":
    unittest.main()
