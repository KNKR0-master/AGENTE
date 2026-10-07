"""Bounded local processes used only by the opt-in headline renderer."""
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import threading

from src.media_tools import resolve_ffprobe_path


class RenderError(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)


def sha256(path):
    digest = hashlib.sha256()
    with Path(path).open('rb') as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b''):
            digest.update(chunk)
    return digest.hexdigest()


def run(args, *, cwd=None, timeout=30, limit=1024 * 1024):
    """Drain both pipes without unbounded communicate() buffers or shell parsing."""
    if not 0 < timeout <= 600 or not 0 < limit <= 8 * 1024 * 1024:
        raise RenderError('HEADLINE_PROCESS_LIMIT_INVALID')
    try:
        process = subprocess.Popen([str(a) for a in args], cwd=cwd, shell=False,
                                   stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL)
    except OSError as exc:
        raise RenderError('HEADLINE_PROCESS_UNAVAILABLE') from exc
    buffers = [bytearray(), bytearray()]
    overflow = [False, False]

    def drain(stream, index, capacity):
        with stream:
            for chunk in iter(lambda: stream.read(8192), b''):
                remaining = max(0, capacity - len(buffers[index]))
                buffers[index].extend(chunk[:remaining])
                overflow[index] |= len(chunk) > remaining

    threads = [threading.Thread(target=drain, args=(pipe, i, limit if i == 0 else 8192))
               for i, pipe in enumerate((process.stdout, process.stderr))]
    for thread in threads:
        thread.start()
    timed_out = False
    try:
        process.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        timed_out = True
        process.kill()  # Only the renderer child we own; never browser/runtime processes.
        process.wait(timeout=5)
    finally:
        for thread in threads:
            thread.join(timeout=5)
    if timed_out:
        raise RenderError('HEADLINE_PROCESS_TIMEOUT')
    if process.returncode:
        # Raw stderr can echo text/paths. Keep the public and persisted error sanitized.
        raise RenderError('HEADLINE_PROCESS_EXIT_ERROR')
    if overflow[0]:
        raise RenderError('HEADLINE_PROCESS_OUTPUT_LIMIT')
    return bytes(buffers[0]).decode('utf-8', errors='replace')


def resolve_ffmpeg(root, explicit=None):
    configured = explicit
    if not configured:
        for name in ('media_tools.local.json', 'media_tools.json'):
            path = Path(root) / 'config' / name
            if path.is_file():
                configured = json.loads(path.read_text(encoding='utf-8')).get('ffmpeg_path')
                if configured:
                    break
    configured = configured or os.environ.get('FFMPEG_PATH')
    if configured:
        path = Path(os.path.expandvars(os.path.expanduser(str(configured)))).resolve()
        if not path.is_file():
            raise RenderError('HEADLINE_FFMPEG_UNAVAILABLE')
        return path
    probe = resolve_ffprobe_path(root)
    sibling = probe.with_name('ffmpeg.exe' if os.name == 'nt' else 'ffmpeg') if probe else None
    if sibling and sibling.is_file():
        return sibling
    found = shutil.which('ffmpeg')
    if not found:
        raise RenderError('HEADLINE_FFMPEG_UNAVAILABLE')
    return Path(found).resolve()


_CAPABILITIES = {}
_CAPABILITY_LOCK = threading.Lock()


def capabilities(ffmpeg):
    identity = sha256(ffmpeg)
    with _CAPABILITY_LOCK:
        if identity in _CAPABILITIES:
            return _CAPABILITIES[identity]
        version = run([ffmpeg, '-version']).splitlines()[0]
        filters = run([ffmpeg, '-hide_banner', '-filters'])
        encoders = run([ffmpeg, '-hide_banner', '-encoders'])
        muxers = run([ffmpeg, '-hide_banner', '-muxers'])
        help_text = run([ffmpeg, '-hide_banner', '-h', 'filter=drawtext'])
        if (any(item not in filters for item in ('drawtext', 'gblur', 'overlay')) or
                any(item not in encoders for item in ('libx264', 'aac', 'png')) or
                'mp4' not in muxers or any(item not in help_text for item in ('textfile', 'expansion', 'text_shaping'))):
            raise RenderError('HEADLINE_FFMPEG_CAPABILITY_MISSING')
        result = dict(binary_sha256=identity, version=version)
        _CAPABILITIES[identity] = result
        return result


def probe(ffprobe, path):
    try:
        result = json.loads(run([ffprobe, '-v', 'error', '-show_streams', '-show_format', '-of', 'json', path]))
        videos = [s for s in result['streams'] if s['codec_type'] == 'video']
        if len(videos) != 1:
            raise ValueError()
        video = videos[0]
        rotation = float(video.get('tags', {}).get('rotate', 0))
        for side in video.get('side_data_list', []):
            rotation = float(side.get('rotation', rotation))
        rotation %= 360
        if rotation not in (0, 90, 180, 270) or video.get('sample_aspect_ratio', '1:1') not in ('1:1', 'N/A'):
            raise RenderError('HEADLINE_ORIENTATION_UNSUPPORTED')
        width, height = int(video['width']), int(video['height'])
        display = [height, width] if rotation in (90, 270) else [width, height]
        duration = float(result.get('format', {}).get('duration', video.get('duration', 0)))
        return dict(duration=duration, coded=[width, height], display=display, rotation=rotation,
                    video=video, audio=[s for s in result['streams'] if s['codec_type'] == 'audio'])
    except (ValueError, KeyError, TypeError) as exc:
        if isinstance(exc, RenderError):
            raise
        raise RenderError('HEADLINE_MEDIA_INVALID') from exc
