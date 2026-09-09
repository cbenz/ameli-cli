"""Default path resolution following the XDG specifications.

No application state is created inside the source directory. Everything is
stored under the standard XDG directories:
- dedicated Chrome profile (persisted session) → $XDG_STATE_HOME/ameli-cli/chrome
- session cache                             → $XDG_STATE_HOME/ameli-cli/session.json
- downloaded statements                     → ~/Documents/ameli
"""

import os
from pathlib import Path

_APP = "ameli-cli"


def _home() -> Path:
    return Path(os.environ.get("HOME", "~")).expanduser()


def xdg_data_home() -> Path:
    return Path(os.environ.get("XDG_DATA_HOME") or (_home() / ".local" / "share"))


def xdg_state_home() -> Path:
    return Path(os.environ.get("XDG_STATE_HOME") or (_home() / ".local" / "state"))


def xdg_cache_home() -> Path:
    return Path(os.environ.get("XDG_CACHE_HOME") or (_home() / ".cache"))


def documents_dir() -> Path:
    """The ~/Documents directory (downloaded statements belong to the user)."""
    return _home() / "Documents"


def download_dir() -> Path:
    """Download base directory (one sub-folder per collection)."""
    return documents_dir() / "ameli"


def chrome_dir() -> Path:
    """Dedicated Chrome user-data-dir used for the login (started with
    `--remote-debugging-port`). Lives in the state dir so the portal session
    persists between runs while staying separate from the user's own Chrome."""
    return xdg_state_home() / _APP / "chrome"


def session_cache_path() -> Path:
    """Session cache (reusable authentication state: portal session cookies)."""
    return xdg_state_home() / _APP / "session.json"
