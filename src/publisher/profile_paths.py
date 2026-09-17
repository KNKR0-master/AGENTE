"""Canonical browser-profile paths for the Publisher runtime."""

from __future__ import annotations

import os
import re
from pathlib import Path


PROFILE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def browser_profile_root(*, environ=None) -> Path:
    env = os.environ if environ is None else environ
    override = env.get("PUBLISHER_BROWSER_PROFILE_ROOT")
    if override:
        return Path(override).expanduser().resolve()
    local_app_data = env.get("LOCALAPPDATA")
    if not local_app_data:
        raise RuntimeError("LOCALAPPDATA_REQUIRED_FOR_BROWSER_PROFILE")
    return (Path(local_app_data) / "PublisherDesk" / "browser_profiles").resolve()


def browser_profile_id(reference: str) -> str:
    """Extract the stable profile id from legacy paths or id-only records."""
    profile_id = Path(str(reference or "").replace("\\", "/")).name
    if not PROFILE_ID.fullmatch(profile_id):
        raise ValueError("INVALID_BROWSER_PROFILE_ID")
    return profile_id


def resolve_browser_profile(reference: str, *, environ=None) -> Path:
    return (browser_profile_root(environ=environ) / browser_profile_id(reference)).resolve()
