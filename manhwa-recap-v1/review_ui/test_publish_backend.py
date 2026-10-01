
import os, sys
sys.path.insert(0, "manhwa-recap-v1/review_ui")
import server
import upload_post

def test_routes_exist():
    routes = [r.path for r in server.app.routes]
    assert "/api/outstand/status" in routes
    assert "/api/outstand/connect" in routes
    assert "/api/outstand/publish" in routes
    assert "/api/outstand/refresh" in routes

def test_backend_switch():
    os.environ["UPLOAD_POST_API_KEY"] = "test_key_xyz"
    backend, mod = server._get_publish_backend()
    assert backend == "upload_post"
    assert mod.__name__ == "upload_post"

    del os.environ["UPLOAD_POST_API_KEY"]
    backend, mod = server._get_publish_backend()
    assert backend == "outstand"
    assert mod.__name__ == "outstand"
    print("Backend switcher verified!")

if __name__ == "__main__":
    test_routes_exist()
    test_backend_switch()
