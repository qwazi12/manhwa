"""Owner rule 2026-11-07 (HEAD_RULES_20261107T1345Z): every video goes live at HH:00 ET.
The scheduled upload carries YouTube's own publish time and is private until then.
No network: the HTTP call is faked. Run: python3 -m pytest test_top_of_hour.py -q"""
import os
import sys

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, HERE)
import upload_post  # noqa: E402


def _fields(tmp_path, md):
    video = tmp_path / "v.mp4"
    video.write_bytes(b"x" * 64)
    sent = []
    cfg = {**upload_post.config(), "api_key": "test-key", "configured": True}
    upload_post.upload_video_post(str(video), md, ["youtube"], cfg=cfg,
                                  _http=lambda method, url, body, cfg: sent.append(body) or {"success": True})
    return sent[0].decode("utf-8", "replace")


def test_scheduled_upload_sends_youtube_publish_time_and_private(tmp_path):
    body = _fields(tmp_path, {"title": "T", "privacy": "public", "youtube_publish_at": "2026-11-07T19:00:00Z"})
    assert 'name="youtube_publish_at"\r\n\r\n2026-11-07T19:00:00Z' in body
    assert 'name="privacyStatus"\r\n\r\nprivate' in body          # private until YouTube publishes it


def test_post_now_is_unchanged(tmp_path):
    body = _fields(tmp_path, {"title": "T", "privacy": "public"})
    assert "youtube_publish_at" not in body and 'name="privacyStatus"\r\n\r\npublic' in body
