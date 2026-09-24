"""Locate a local Chrome binary for DOM regression tests (macOS or Linux).

Universal ID: code:server-deploy-001:test-chrome-path
"""

import os
import shutil
from pathlib import Path

_CANDIDATES = (
    "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome",
    str(Path.home() / ".local/opt/google-chrome/google-chrome"),
)


def local_chrome() -> str:
    """PARSER_TEST_CHROME wins; otherwise the first installed Chrome/Chromium."""
    if os.environ.get("PARSER_TEST_CHROME"):
        return os.environ["PARSER_TEST_CHROME"]
    for candidate in _CANDIDATES:
        if Path(candidate).exists():
            return candidate
    for name in ("google-chrome-stable", "google-chrome", "chromium", "chromium-browser"):
        found = shutil.which(name)
        if found:
            return found
    return _CANDIDATES[0]
