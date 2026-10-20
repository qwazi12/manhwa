"""pytest collection rules for review_ui.

Two kinds of test files live here: pytest suites (`def test_...`) and
script suites that run on import and end with sys.exit (`python3 test_x.py`,
see AGENTS.md). Collecting a script suite executes it and its SystemExit
aborts the whole session, so plain `pytest` only collects real pytest files.
"""
import os


def pytest_ignore_collect(collection_path, config):
    name = os.path.basename(str(collection_path))
    if not (name.startswith("test_") and name.endswith(".py")):
        return None
    try:
        with open(collection_path, encoding="utf-8") as f:
            text = f.read()
    except OSError:
        return None
    return True if "\ndef test_" not in "\n" + text else None
