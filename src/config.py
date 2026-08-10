from __future__ import annotations

import os
from pathlib import Path


def load_env_file(path: str | Path = ".env") -> None:
    """Load simple KEY=VALUE entries without overriding the process environment."""
    source = Path(path)
    if not source.exists():
        return
    for raw_line in source.read_text(encoding="utf-8-sig").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, value = line.split("=", 1)
        key = key.strip()
        value = value.strip()
        if value and value[0] in {'"', "'"} and value[-1:] == value[0]:
            value = value[1:-1]
        if key:
            os.environ.setdefault(key, value)
