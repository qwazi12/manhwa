
import os, sys
sys.path.insert(0, "manhwa-recap-v1/review_ui")
import upload_post

def test_mk_profile_defaults(monkeypatch):
    # Clear every spelling the config reads, whatever an earlier test left set.
    for k in ("UPLOADPOST_API_KEY", "UPLOAD_POST_API_KEY", "UPLOADPOST_PROFILE", "UPLOAD_POST_PROFILE"):
        monkeypatch.delenv(k, raising=False)

    monkeypatch.setenv("UPLOADPOST_API_KEY", "live_key_test")
    cfg = upload_post.config()
    assert cfg["configured"] == True
    assert cfg["api_key"] == "live_key_test"
    assert cfg["user_profile"] == "mk"
    print("test_mk_profile_defaults passed!")

if __name__ == "__main__":
    import pytest
    with pytest.MonkeyPatch.context() as mp:
        test_mk_profile_defaults(mp)
