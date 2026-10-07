"""Portable, explicit local renderer configuration and machine readiness."""
import json
import os
from pathlib import Path

from src.publisher.headline_tools import RenderError, capabilities, sha256, run


def load_configuration(root):
    path = Path(root) / 'config/headline_renderer.json'
    if not path.is_file():
        raise RenderError('HEADLINE_RENDERER_UNAVAILABLE')
    data = json.loads(path.read_text(encoding='utf-8'))
    if data.get('enabled', True) is not True:
        raise RenderError('HEADLINE_RENDERER_DISABLED')
    return data


def asset_path(root, value):
    path = Path(os.path.expandvars(os.path.expanduser(str(value))))
    return (path if path.is_absolute() else Path(root) / path).resolve()


def create_renderer(root, input_root, cancel_event=None):
    from src.publisher.headline_renderer import HeadlineRenderer
    from src.publisher.upload_preparation import artifact_root
    data = load_configuration(root)
    fonts = {key: asset_path(root, value['path'] if isinstance(value, dict) else value)
             for key, value in data.get('fonts', {}).items()}
    return HeadlineRenderer(root, input_root, artifact_root(root), fonts,
        ffmpeg=asset_path(root, data['ffmpeg_path']) if data.get('ffmpeg_path') else None,
        ffprobe=asset_path(root, data['ffprobe_path']) if data.get('ffprobe_path') else None,
        timeout=data.get('timeout_seconds', 120), cancel_event=cancel_event)


def classic_font(root):
    from fontTools.ttLib import TTFont
    data = load_configuration(root)
    reference = data.get('classic_font_ref', 'classic')
    font = data.get('fonts', {}).get(reference)
    if not isinstance(font, dict) or not font.get('path') or not font.get('sha256'):
        raise RenderError('HEADLINE_CLASSIC_FONT_UNAVAILABLE')
    path = asset_path(root, font['path'])
    if not path.is_file():
        raise RenderError('HEADLINE_CLASSIC_FONT_UNAVAILABLE')
    if sha256(path) != font['sha256'].lower():
        raise RenderError('HEADLINE_FONT_HASH_MISMATCH')
    face = int(font.get('face_index', 0))
    with TTFont(path, fontNumber=face) as loaded:
        if 'fvar' in loaded:
            raise RenderError('HEADLINE_FONT_WEIGHT_UNSUPPORTED')
        weight = loaded['OS/2'].usWeightClass
    return dict(font_ref=reference, font_sha256=font['sha256'].lower(),
                font_face_index=face, font_weight=weight)


def machine_readiness(root):
    try:
        font = classic_font(root)
        renderer = create_renderer(root, Path(root) / 'data/publisher/temp')
        run([renderer.ffprobe, '-version'])
        identity = capabilities(renderer.ffmpeg)
        return dict(ready=True, label='Pronto', code=None, classic_font=font,
                    ffmpeg_sha256=identity['binary_sha256'], ffprobe_sha256=sha256(renderer.ffprobe))
    except (OSError, ValueError, TypeError, KeyError) as exc:
        return dict(ready=False, label='Configurar renderer',
                    code=getattr(exc, 'code', 'HEADLINE_RENDERER_CONFIG_INVALID'))
