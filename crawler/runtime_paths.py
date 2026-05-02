from __future__ import annotations

import os
from pathlib import Path


CRAWLER_ROOT = Path(__file__).resolve().parent
DEFAULT_BROWSER_PROFILE_ROOT = CRAWLER_ROOT / "browser_profile"


def get_browser_profile_root() -> Path:
    raw_value = (os.environ.get("MYSELLCOMB_BROWSER_PROFILE_ROOT") or "").strip()
    if not raw_value:
        return DEFAULT_BROWSER_PROFILE_ROOT

    profile_root = Path(raw_value)
    if not profile_root.is_absolute():
        profile_root = (CRAWLER_ROOT.parent / profile_root).resolve()
    return profile_root


def get_browser_profile_dir(profile_name: str) -> Path:
    return get_browser_profile_root() / profile_name
