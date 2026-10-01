import os, sys
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import server
import upload_post


def test_routes_exist():
    routes = [r.path for r in server.app.routes]
    for p in ("status", "connect", "publish", "refresh", "eligibility",
              "publish/status", "disconnect"):
        assert "/api/publishing/" + p in routes, p
    assert not [r for r in routes if "outstand" in r], "Outstand routes remain"


def test_backend_is_upload_post_only():
    for k in ("UPLOADPOST_API_KEY", "UPLOAD_POST_API_KEY", "UPLOADPOST_KEY"):
        os.environ.pop(k, None)
    backend, mod = server._get_publish_backend()
    assert backend == "upload_post" and mod is upload_post
    # Unconfigured: no fallback provider, publishing is blocked with a reason.
    st = mod.accounts_status(server._os_root())
    assert st["configured"] is False and st["can_publish"] is False
    os.environ["UPLOAD_POST_API_KEY"] = "test_key_xyz"
    assert server._get_publish_backend()[0] == "upload_post"
    del os.environ["UPLOAD_POST_API_KEY"]
    try:
        import outstand  # noqa: F401
        raise AssertionError("outstand module still importable")
    except ImportError:
        pass
    print("Backend is Upload-Post only — verified!")


if __name__ == "__main__":
    test_routes_exist()
    test_backend_is_upload_post_only()
