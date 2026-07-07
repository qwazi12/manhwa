import os, sys
sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..",
                                "manhwa-recap-v1", "review_ui"))
from server import app  # noqa: E402  (FastAPI ASGI app)
