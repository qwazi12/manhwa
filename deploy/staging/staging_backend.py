"""Offline-only wrapper around the actual integrated FastAPI application.

No mock projects, media or generation responses. Publishing stays unavailable;
queue scheduling is not presented as a completed publishing simulation.
"""
import os

from starlette.responses import JSONResponse


def blocked(method, path):
    if path.startswith(("/api/youtube", "/api/publishing", "/api/drive")):
        return True  # includes OAuth GETs
    if method in ("GET", "HEAD", "OPTIONS"):
        return False
    if path.startswith(("/api/autopilot", "/api/backup", "/api/studio/queue")):
        return True
    return path in {"/api/settings", "/api/schedule", "/api/studio/bulk", "/api/studio/stats",
                    "/api/spend/cap", "/api/jobs/resume"}


class OfflineBoundary:
    def __init__(self, app):
        self.application = app

    async def __call__(self, scope, receive, send):
        if scope["type"] == "http" and blocked(scope["method"], scope["path"]):
            response = JSONResponse({"code": "staging_disabled", "detail":
                "Offline staging: publishing, accounts, automatic jobs and posting schedules are disabled. "
                "Queue simulation has not been implemented."}, status_code=403)
            return await response(scope, receive, send)
        return await self.application(scope, receive, send)


def create_app():
    if os.environ.get("FLAMINGO_STAGING") != "1":
        raise RuntimeError("Use the isolated staging launcher")
    if any(os.environ.get(key) for key in ("GEMINI_API_KEY", "TTS_API_KEY", "YOUTUBE_API_KEY",
                                           "UPLOAD_POST_API_KEY", "GOOGLE_APPLICATION_CREDENTIALS")):
        raise RuntimeError("Offline staging must not contain external service credentials")
    import server
    return OfflineBoundary(server.app)


if os.environ.get("FLAMINGO_STAGING") == "1":
    app = create_app()
