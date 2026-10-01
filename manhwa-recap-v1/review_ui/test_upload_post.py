
import os, sys
sys.path.insert(0, os.path.dirname(__file__))
import upload_post

def test_config():
    os.environ["UPLOAD_POST_API_KEY"] = "test_key_123"
    os.environ["UPLOAD_POST_PROFILE"] = "my_profile"
    cfg = upload_post.config()
    assert cfg["configured"] == True
    assert cfg["api_key"] == "test_key_123"
    assert cfg["user_profile"] == "my_profile"

def test_multipart_builder():
    fields = [("user", "test_user"), ("title", "Awesome Video")]
    files = [("video", "test.mp4", "video/mp4", b"dummy video content")]
    body, ct = upload_post._encode_multipart_formdata(fields, files)
    assert b"dummy video content" in body
    assert b"Awesome Video" in body
    assert "multipart/form-data; boundary=" in ct

def test_upload_post_mock():
    os.environ["UPLOAD_POST_API_KEY"] = "test_key_123"
    called = []
    def mock_http(method, url, body, cfg):
        called.append((method, url, len(body)))
        return {"success": True, "post_id": "test_post_999"}

    # Create dummy video
    dummy_path = "/tmp/test_dummy_video.mp4"
    with open(dummy_path, "wb") as f:
        f.write(b"mp4data"*100)

    res = upload_post.upload_video_post(
        dummy_path,
        {"title": "Test Title", "description": "Test Desc", "privacy": "private"},
        ["youtube", "tiktok"],
        _http=mock_http
    )
    assert res["success"] == True
    assert res["post_id"] == "test_post_999"
    assert len(called) == 1
    assert "api.upload-post.com/api/upload" in called[0][1]

    if os.path.exists(dummy_path):
        os.remove(dummy_path)
    print("All unit tests in test_upload_post.py passed!")

if __name__ == "__main__":
    test_config()
    test_multipart_builder()
    test_upload_post_mock()
