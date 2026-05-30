"""Load private local API keys from a user-private env file.

This keeps provider secrets outside the repo while letting local backend jobs
use the same keys without requiring `export ...` in every new terminal.
"""

from __future__ import annotations

import os
from pathlib import Path

DEFAULT_PATH = Path.home() / (".wealth-" + "dashboard") / "secrets.env"


def load_private_env(path: Path | None = None, *, override: bool = False) -> dict[str, str]:
    """Load KEY=VALUE lines into os.environ and return loaded values.

    Existing process environment values win by default, so temporary shell
    overrides still work. Blank values and comments are ignored.
    """
    p = path or DEFAULT_PATH
    loaded: dict[str, str] = {}
    try:
        lines = p.read_text().splitlines()
    except OSError:
        return loaded

    for raw in lines:
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        if not key or not value:
            continue
        loaded[key] = value
        if override or not os.environ.get(key):
            os.environ[key] = value
    return loaded
