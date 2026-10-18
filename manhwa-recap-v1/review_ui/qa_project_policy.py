"""An explicit project-local QA marker, independent of global QA calibration.

Operators create this marker before starting an isolated live QA project.
Existence is fail-closed, including malformed files or symlinks. Normal
projects are unaffected; publishing destinations/settings never change.
"""
import os

MARKER = ".flamingo-qa-only"
REASON = "QA-only project: publishing, scheduling and external uploads are disabled."


def restricted(pdir):
    return os.path.lexists(os.path.join(os.fspath(pdir), MARKER))
