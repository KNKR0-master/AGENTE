"""Explicit local H2 renderer. Not initialized by Publisher or called by workers."""
from fractions import Fraction
import hashlib
import json
from pathlib import Path
import tempfile
import uuid

import fontTools

from src.media_tools import resolve_ffprobe_path
from src.store import now_iso
from src.publisher.headlines import canonical, readiness
from src.publisher.headline_layout import layout, filters
from src.publisher.headline_tools import RenderError, capabilities, probe, resolve_ffmpeg, run, sha256

POLICY = dict(version='headline-renderer-1', codec='libx264', preset='medium', crf=18,
              pixel_format='yuv420p', audio='copy-aac-otherwise-aac-192k', cadence='passthrough',
              preview='quarter-duration-capped-at-one-second-v1', fonttools=fontTools.__version__)


def init_artifacts(conn):
    """Explicit additive migration for isolated H2 databases only; no startup hook."""
    conn.executescript('''
    CREATE TABLE IF NOT EXISTS publisher_derived_media_artifacts (
      id TEXT PRIMARY KEY, draft_id INTEGER NOT NULL, kind TEXT NOT NULL,
      cache_key TEXT NOT NULL, source_sha256 TEXT NOT NULL, render_spec_hash TEXT NOT NULL,
      renderer_version TEXT NOT NULL, state TEXT NOT NULL CHECK(state IN ('GENERATING','READY','FAILED')),
      local_path TEXT, output_sha256 TEXT, probe_json TEXT, layout_json TEXT,
      error_code TEXT, created_at TEXT NOT NULL, ready_at TEXT);
    CREATE INDEX IF NOT EXISTS headline_artifact_cache ON publisher_derived_media_artifacts(cache_key,state);
    ''')


def inside(path, root):
    path, root = Path(path).resolve(), Path(root).resolve()
    if not path.is_relative_to(root) or path == root:
        raise RenderError('HEADLINE_PATH_REJECTED')
    return path


def frame_period(media):
    try:
        rate = Fraction(media['video']['avg_frame_rate'])
        if rate <= 0:
            raise ValueError()
        return float(1 / rate)
    except (KeyError, ValueError, ZeroDivisionError):
        raise RenderError('HEADLINE_TIMING_UNSUPPORTED')


def validate_output(source, output, kind):
    if output['display'] != source['display'] or output['rotation']:
        raise RenderError('HEADLINE_OUTPUT_DIMENSIONS_INVALID')
    video = output['video']
    if kind == 'preview':
        if video['codec_name'] != 'png':
            raise RenderError('HEADLINE_OUTPUT_CODEC_INVALID')
        return
    if video['codec_name'] != 'h264' or video['pix_fmt'] != 'yuv420p':
        raise RenderError('HEADLINE_OUTPUT_CODEC_INVALID')
    if len(output['audio']) != len(source['audio']):
        raise RenderError('HEADLINE_OUTPUT_AUDIO_INVALID')
    for original, rendered in zip(source['audio'], output['audio']):
        if (rendered['codec_name'] != 'aac' or rendered['channels'] != original['channels'] or
                rendered['sample_rate'] != original['sample_rate']):
            raise RenderError('HEADLINE_OUTPUT_AUDIO_INVALID')
    # One video tick/frame plus one AAC access unit accounts for muxer rounding
    # and priming. Cadence itself is additionally validated with packet timestamps.
    audio_tick = max([1024 / int(a['sample_rate']) for a in source['audio']] or [0])
    tolerance = max(frame_period(source), frame_period(output)) + audio_tick
    if abs(output['duration'] - source['duration']) > tolerance:
        raise RenderError('HEADLINE_OUTPUT_DURATION_INVALID')


class HeadlineRenderer:
    def __init__(self, root, input_root, artifact_root, fonts, *, ffmpeg=None, ffprobe=None, timeout=120, cancel_event=None):
        self.root = Path(root)
        self.input_root = Path(input_root).resolve()
        self.artifact_root = Path(artifact_root).resolve()
        # Prevent output under inputs or tracked repository paths. H2 artifacts
        # belong in OS TEMP, not alongside materialized source media.
        temp_root = Path(tempfile.gettempdir()).resolve()
        inside(self.artifact_root, temp_root)
        if self.artifact_root.is_relative_to(self.input_root):
            raise RenderError('HEADLINE_ARTIFACT_ROOT_INVALID')
        self.fonts = dict(fonts)
        self.ffmpeg = resolve_ffmpeg(root, ffmpeg)
        self.ffprobe = ffprobe or resolve_ffprobe_path(root)
        if not self.ffprobe:
            raise RenderError('HEADLINE_FFPROBE_UNAVAILABLE')
        self.timeout = timeout
        self.cancel_event = cancel_event
        self.identity = {**POLICY, **capabilities(self.ffmpeg), 'ffprobe_sha256': sha256(self.ffprobe)}
        self.version = hashlib.sha256(canonical(self.identity).encode()).hexdigest()

    def timestamps(self, path):
        data = json.loads(run([self.ffprobe, '-v', 'error', '-select_streams', 'v:0',
                               '-show_packets', '-show_entries', 'packet=pts_time', '-of', 'json', path],
                              timeout=self.timeout, limit=8 * 1024 * 1024, cancel_event=self.cancel_event))
        values = sorted(float(p['pts_time']) for p in data['packets'])
        if not values:
            raise RenderError('HEADLINE_TIMING_UNSUPPORTED')
        return [v - values[0] for v in values]

    def validate_file(self, source, source_path, output_path, kind):
        if not output_path.is_file() or not output_path.stat().st_size:
            raise RenderError('HEADLINE_OUTPUT_INVALID')
        output = probe(self.ffprobe, output_path, cancel_event=self.cancel_event)
        validate_output(source, output, kind)
        run([self.ffmpeg, '-v', 'error', '-xerror', '-i', output_path, '-f', 'null', '-'], timeout=self.timeout, cancel_event=self.cancel_event)
        if kind == 'video':
            before, after = self.timestamps(source_path), self.timestamps(output_path)
            tick = max(float(Fraction(source['video']['time_base'])), float(Fraction(output['video']['time_base'])))
            if len(before) != len(after) or any(abs(a-b) > tick * 2 for a, b in zip(before, after)):
                raise RenderError('HEADLINE_OUTPUT_CADENCE_INVALID')
        return output

    def render(self, conn, draft_id, snapshot, source_path, *, kind='video', force=False):
        errors = readiness(snapshot)
        if errors:
            raise RenderError(errors[0])
        if not snapshot['enabled']:
            return {'state': 'DERIVED_ARTIFACT_NOT_REQUIRED'}
        if (sha256(self.ffmpeg) != self.identity['binary_sha256'] or
                sha256(self.ffprobe) != self.identity['ffprobe_sha256']):
            raise RenderError('HEADLINE_TOOL_IDENTITY_CHANGED')
        if kind not in ('video', 'preview') or type(draft_id) is not int or draft_id < 1:
            raise RenderError('HEADLINE_REQUEST_INVALID')
        source_path = inside(source_path, self.input_root)
        if not source_path.is_file():
            raise RenderError('HEADLINE_LOCAL_SOURCE_REQUIRED')
        source_hash = sha256(source_path)
        source = probe(self.ffprobe, source_path, cancel_event=self.cancel_event)
        if source['duration'] <= 0 or any(d <= 0 or d % 2 for d in source['display']):
            raise RenderError('HEADLINE_MEDIA_UNSUPPORTED')
        key = hashlib.sha256(canonical([draft_id, source_hash, snapshot['render_spec_hash'], self.version, kind]).encode()).hexdigest()
        if not force:
            for row in conn.execute("SELECT * FROM publisher_derived_media_artifacts WHERE cache_key=? AND state='READY' ORDER BY created_at DESC", (key,)).fetchall():
                row = dict(row)
                try:
                    path = inside(row['local_path'], self.artifact_root)
                    if not path.is_file() or sha256(path) != row['output_sha256']:
                        raise RenderError('HEADLINE_CACHE_INVALID')
                    self.validate_file(source, source_path, path, kind)
                    return {**row, 'reused': True}
                except (RenderError, OSError):
                    with conn:
                        conn.execute("UPDATE publisher_derived_media_artifacts SET state='FAILED',error_code='HEADLINE_CACHE_INVALID' WHERE id=?", (row['id'],))
        artifact_id = uuid.uuid4().hex
        with conn:
            conn.execute('INSERT INTO publisher_derived_media_artifacts(id,draft_id,kind,cache_key,source_sha256,render_spec_hash,renderer_version,state,created_at) VALUES (?,?,?,?,?,?,?,?,?)',
                         (artifact_id, draft_id, kind, key, source_hash, snapshot['render_spec_hash'], self.version, 'GENERATING', now_iso()))
        workspace = inside(self.artifact_root / artifact_id, self.artifact_root)
        workspace.mkdir(parents=True, exist_ok=False)
        partial = workspace / 'output.partial'
        final = workspace / ('preview.png' if kind == 'preview' else 'video.mp4')
        try:
            plan = layout(snapshot['text'], snapshot['preset']['spec'], source['display'], self.fonts, workspace)
            graph = filters(plan, workspace)
            args = [self.ffmpeg, '-nostdin', '-hide_banner', '-v', 'error', '-y', '-i', source_path,
                    '-filter_complex_threads', '1', '-filter_complex', graph, '-map', '[out]', '-map_metadata', '-1',
                    '-metadata:s:v:0', 'rotate=0', '-vsync', '0', '-threads', '1']
            if kind == 'preview':
                # Pick an existing frame nearest the policy timestamp. A single-
                # frame clip must select zero, not seek beyond its only frame.
                desired = min(1.0, source['duration'] / 4)
                timestamp = min(self.timestamps(source_path), key=lambda value: (abs(value-desired), value))
                args += ['-ss', str(timestamp), '-frames:v', '1', '-an', '-c:v', 'png', '-f', 'image2']
            else:
                args += ['-map', '0:a?', '-c:v', 'libx264', '-preset', POLICY['preset'], '-crf', str(POLICY['crf']), '-pix_fmt', 'yuv420p']
                for index, audio in enumerate(source['audio']):
                    args += [f'-c:a:{index}', 'copy' if audio['codec_name'] == 'aac' else 'aac']
                    if audio['codec_name'] != 'aac':
                        args += [f'-b:a:{index}', '192k']
                args += ['-movflags', '+faststart', '-f', 'mp4']
            run([*args, partial], cwd=workspace, timeout=self.timeout, cancel_event=self.cancel_event)
            output = self.validate_file(source, source_path, partial, kind)
            if sha256(source_path) != source_hash:
                raise RenderError('HEADLINE_SOURCE_CHANGED')
            if self.cancel_event is not None and self.cancel_event.is_set():
                raise RenderError('HEADLINE_PREPARATION_CANCELLED')
            digest = sha256(partial)
            partial.replace(final)
            with conn:
                conn.execute("UPDATE publisher_derived_media_artifacts SET state='READY',local_path=?,output_sha256=?,probe_json=?,layout_json=?,ready_at=? WHERE id=?",
                             (str(final), digest, canonical(output), canonical(plan), now_iso(), artifact_id))
            return {**dict(conn.execute('SELECT * FROM publisher_derived_media_artifacts WHERE id=?', (artifact_id,)).fetchone()), 'reused': False}
        except Exception as exc:
            code = exc.code if isinstance(exc, RenderError) else 'HEADLINE_RENDER_FAILED'
            with conn:
                conn.execute("UPDATE publisher_derived_media_artifacts SET state='FAILED',error_code=? WHERE id=?", (code, artifact_id))
            raise RenderError(code) from exc
