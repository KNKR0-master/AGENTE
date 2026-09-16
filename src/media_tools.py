"""Local media-tool discovery and preflight checks."""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from pathlib import Path


class FFProbeError(RuntimeError):
    def __init__(self, code, message, *, exception_type=None, winerror=None, process_started=None):
        super().__init__(message)
        self.code = code
        self.exception_type = exception_type
        self.winerror = winerror
        self.process_started = process_started


def _expand_path(value):
    return Path(os.path.expandvars(os.path.expanduser(str(value)))).resolve()


def _configured_value(root):
    explicit = os.getenv('FFPROBE_PATH')
    if explicit:
        return explicit
    for name in ('media_tools.local.json', 'media_tools.json'):
        path = Path(root) / 'config' / name
        if not path.is_file():
            continue
        try:
            value = json.loads(path.read_text(encoding='utf-8')).get('ffprobe_path')
        except (OSError, ValueError):
            continue
        if value:
            return value
    return None


def resolve_ffprobe_path(root=None):
    """Resolve explicit env, persisted app config, then PATH fallback."""
    configured = _configured_value(root or Path.cwd())
    if configured:
        candidate = _expand_path(configured)
        try:
            is_file = candidate.is_file()
        except OSError:
            # Keep the configured candidate so diagnostics can distinguish an
            # inaccessible path from a missing executable.
            return candidate
        if is_file:
            return candidate
        return None
    discovered = shutil.which('ffprobe')
    return Path(discovered).resolve() if discovered else None


def ffprobe_version(path, timeout=10):
    if not path:
        raise FFProbeError('FFPROBE_NOT_FOUND', 'ffprobe nao encontrado', process_started=False)
    try:
        completed = subprocess.run(
            [str(path), '-version'],
            capture_output=True,
            text=True,
            shell=False,
            check=False,
            timeout=timeout,
        )
    except FileNotFoundError as exc:
        raise FFProbeError(
            'FFPROBE_NOT_FOUND', 'ffprobe nao encontrado',
            exception_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None),
            process_started=False,
        ) from exc
    except subprocess.TimeoutExpired as exc:
        raise FFProbeError(
            'FFPROBE_TIMEOUT', 'ffprobe excedeu o timeout de inicializacao',
            exception_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None),
            process_started=True,
        ) from exc
    except OSError as exc:
        raise FFProbeError(
            'FFPROBE_EXIT_ERROR', 'ffprobe nao pode ser iniciado',
            exception_type=type(exc).__name__, winerror=getattr(exc, 'winerror', None),
            process_started=False,
        ) from exc
    if completed.returncode != 0:
        raise FFProbeError('FFPROBE_EXIT_ERROR', f'ffprobe retornou exit code {completed.returncode}')
    first_line = (completed.stdout or completed.stderr or '').splitlines()
    return first_line[0].strip() if first_line else 'ffprobe'


def ensure_ffprobe_available(root, timeout=10):
    path = resolve_ffprobe_path(root)
    ffprobe_version(path, timeout=timeout)
    return path
