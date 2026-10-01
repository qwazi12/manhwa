
import os, sys
sys.path.insert(0, "manhwa-recap-v1/review_ui")
import upload_post

def test_mk_profile_defaults():
    if "UPLOADPOST_API_KEY" in os.environ: del os.environ["UPLOADPOST_API_KEY"]
    if "UPLOAD_POST_API_KEY" in os.environ: del os.environ["UPLOAD_POST_API_KEY"]
    if "UPLOADPOST_PROFILE" in os.environ: del os.environ["UPLOADPOST_PROFILE"]

    os.environ["UPLOADPOST_API_KEY"] = "live_key_test"
    cfg = upload_post.config()
    assert cfg["configured"] == True
    assert cfg["api_key"] == "live_key_test"
    assert cfg["user_profile"] == "mk"
    print("test_mk_profile_defaults passed!")

if __name__ == "__main__":
    test_mk_profile_defaults()
