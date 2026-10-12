"""Canonical demo swatches and read-only style comparisons, outside the app DB."""
import base64
from contextlib import closing
import hashlib
import json
from pathlib import Path
import sqlite3
import threading
import tempfile

from src.publisher import headlines
from src.publisher.headline_renderer import HeadlineRenderer, init_artifacts
from src.publisher.headline_tools import RenderError, run
from src.publisher.headline_layout import layout, filters
from src.publisher.headline_workflow import selection_snapshot, variant

_RENDER_LOCK = threading.Lock()


def local_source_exists(source):
    try:
        return bool(source and Path(source).is_file())
    except OSError as exc:
        raise RenderError('HEADLINE_SOURCE_READ_FAILED') from exc


def image_result(artifact, snapshot):
    if artifact['state'] != 'READY':
        return {'state': artifact['state']}
    path = Path(artifact['local_path'])
    if path.stat().st_size > 8 * 1024 * 1024:
        raise RenderError('HEADLINE_PREVIEW_TOO_LARGE')
    preset = snapshot['preset']
    return {'state': 'READY', 'artifact_id': artifact['id'],
            'preset_name': preset['name'], 'preset_version': preset['version'],
            'render_spec_hash': snapshot['render_spec_hash'],
            'image': 'data:image/png;base64,' + base64.b64encode(path.read_bytes()).decode('ascii')}


def comparison_request(conn, data):
    if not isinstance(data, dict):
        raise ValueError('HEADLINE_PREVIEW_REQUEST_INVALID')
    context = data.get('context')
    selection = data.get('selection') or {}
    if (set(data) - {'context', 'selection'} or not isinstance(context, dict)
            or len(context) != 1 or set(context) - {'draft_id', 'product_id', 'drive_file_id'}
            or not isinstance(selection, dict)):
        raise ValueError('HEADLINE_PREVIEW_REQUEST_INVALID')
    draft = None
    if 'draft_id' in context:
        row = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (int(context['draft_id']),)).fetchone()
        if not row: raise ValueError('DRAFT_NOT_FOUND')
        draft = dict(row)
        snapshot = headlines.read_snapshot(draft)
        if set(selection) - {'preset_id', 'size'}:
            raise ValueError('HEADLINE_PREVIEW_SELECTION_INVALID')
        if selection.get('preset_id') or (selection.get('size') and selection['size'] != snapshot.get('size', 'STANDARD')):
            preset_id = selection.get('preset_id') or (snapshot.get('preset') or {}).get('id')
            snapshot['preset'] = headlines.get_preset(conn, variant(conn, preset_id, selection.get('size', snapshot.get('size', 'STANDARD'))))
            snapshot['render_spec_hash'] = headlines.render_hash(snapshot)
        source = draft.get('temp_path') or draft.get('video_path')
    else:
        if 'drive_file_id' in context:
            from src.publisher.library import get_library_items
            items = get_library_items(conn, [str(context['drive_file_id'])])
            if not items or not items[0].get('product_id'):
                raise ValueError('PRODUCT_NOT_FOUND')
            product_id = items[0]['product_id']
            rows = conn.execute('SELECT d.* FROM publisher_drafts d JOIN publisher_media_assets m '
                                'ON m.id=d.media_asset_id WHERE m.remote_file_id=? ORDER BY d.id DESC',
                                (str(context['drive_file_id']),)).fetchall()
        else:
            product_id = int(context['product_id'])
            rows = conn.execute('SELECT * FROM publisher_drafts WHERE product_id=? ORDER BY id DESC', (product_id,)).fetchall()
        row = conn.execute('SELECT * FROM publisher_products WHERE product_id=?', (product_id,)).fetchone()
        if not row: raise ValueError('PRODUCT_NOT_FOUND')
        snapshot = selection_snapshot(conn, dict(row), selection)
        source = next((r['temp_path'] or r['video_path'] for r in rows
                       if local_source_exists(r['temp_path'] or r['video_path'])), None)
    if not snapshot['enabled']:
        raise RenderError('HEADLINE_DISABLED')
    if not local_source_exists(source):
        raise RenderError('HEADLINE_LOCAL_SOURCE_REQUIRED')
    return snapshot, source


class HeadlinePreviewCache:
    def __init__(self, renderer):
        self.renderer = renderer
        self.root = renderer.artifact_root / 'style-previews'
        try:
            self.root.mkdir(parents=True, exist_ok=True)
        except OSError as exc:
            raise RenderError('HEADLINE_PREVIEW_CACHE_UNAVAILABLE') from exc
        self.db = self.root / 'preview-cache.db'
        self.demo_renderer = None

    def _render(self, renderer, snapshot, source):
        with closing(sqlite3.connect(self.db)) as conn:
            conn.row_factory = sqlite3.Row
            init_artifacts(conn)
            return renderer.render(conn, 1, snapshot, source, kind='preview')

    def compare(self, snapshot, source):
        with _RENDER_LOCK:
            return image_result(self._render(self.renderer, snapshot, source), snapshot)

    def swatch(self, preset):
        snapshot = dict(render_spec_version=1, enabled=True, required=False,
                        text='Classic\nSeu video', preset=preset, provenance='DEMONSTRATION')
        snapshot['render_spec_hash'] = headlines.render_hash(snapshot)
        key = hashlib.sha256(headlines.canonical([snapshot['render_spec_hash'], self.renderer.version,
                                                'swatch-light-dark-320-v2']).encode()).hexdigest()
        with _RENDER_LOCK:
            output = self.root / (key + '.png')
            if not output.is_file():
                # The same verified layout, directly onto a synthetic PNG: no MP4 or cadence validation.
                with tempfile.TemporaryDirectory(prefix='swatch-', dir=self.root) as workspace:
                    plan = layout(snapshot['text'], preset['spec'], (720, 1280), self.renderer.fonts, workspace)
                    _, top, _, height = plan['box']
                    crop_height = min(1280, round(top + height + 32))
                    graph = filters(plan, workspace) + f';[out]crop=720:{crop_height}:0:0,scale=320:-1[sample]'
                    partial = Path(workspace) / 'sample.png'
                    run([self.renderer.ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                         'color=c=0x20252B:size=720x1280:rate=24,drawbox=x=0:y=0:w=iw/2:h=ih:color=0xE9EDF0:t=fill',
                         '-filter_complex_threads', '1', '-filter_complex', graph, '-map', '[sample]',
                         '-threads', '1', '-frames:v', '1', '-c:v', 'png', '-f', 'image2', partial],
                        cwd=workspace, timeout=30)
                    if not partial.read_bytes().startswith(b'\x89PNG\r\n\x1a\n'):
                        raise RenderError('HEADLINE_SWATCH_UNAVAILABLE')
                    partial.replace(output)
            return {'preset_id': preset['id'], 'preset_version': preset['version'],
                    'render_spec_hash': snapshot['render_spec_hash'], 'demonstration': True,
                    'image': 'data:image/png;base64,' + base64.b64encode(output.read_bytes()).decode('ascii')}
