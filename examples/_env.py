"""Load settings from the environment and a .env file (no extra dependency)."""

from __future__ import annotations

import os
from pathlib import Path


def load_settings() -> dict[str, str]:
    settings = dict(os.environ)
    dotenv = Path(__file__).resolve().parent.parent / ".env"
    if dotenv.exists():
        for line in dotenv.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                settings.setdefault(key.strip(), value.strip().strip("'\""))
    return settings
