"""Offline, synthetic-only comparison; never opens the operational database."""
import argparse
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.publisher import headline_renderer as module
from src.publisher.headline_tools import run, resolve_ffmpeg, sha256
from src.publisher.headlines import DEFAULT_SPEC, render_hash


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--output', type=Path, required=True)
    parser.add_argument('--budgets', type=float, nargs='+', default=[120, 600])
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    font = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arialbd.ttf'
    snap = dict(render_spec_version=1, enabled=True, required=False, text='OFERTA DO DIA',
                preset=dict(id=1, version=1, name='Synthetic', spec={**DEFAULT_SPEC,
                    'font_ref': 'fixture', 'font_sha256': sha256(font), 'font_weight': 700,
                    'font_size_ratio': .034, 'text_color': '#FFE500',
                    'background_color': None, 'shadow_color': None, 'stroke_color': None}),
                provenance='DRAFT_OVERRIDE')
    snap['render_spec_hash'] = render_hash(snap)
    results = []
    with tempfile.TemporaryDirectory(prefix='publisher-headline-benchmark-') as directory:
        base = Path(directory)
        inputs = base / 'inputs'
        inputs.mkdir()
        source = inputs / 'synthetic.mp4'
        ffmpeg = resolve_ffmpeg(root)
        run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i',
             'testsrc2=size=1080x1920:rate=30', '-f', 'lavfi', '-i',
             'sine=frequency=440:sample_rate=44100', '-t', '24.1',
             '-vf', 'noise=alls=10:allf=t:all_seed=12345',
             '-c:v', 'libx264', '-preset', 'ultrafast', '-crf', '18',
             '-pix_fmt', 'yuv420p', '-c:a', 'aac', '-ac', '2', source], timeout=120)
        source_hash = sha256(source)
        for budget in args.budgets:
            renderer = module.HeadlineRenderer(root, inputs, base / f'artifacts-{budget}',
                {'fixture': font})
            conn = sqlite3.connect(':memory:')
            conn.row_factory = sqlite3.Row
            module.init_artifacts(conn)
            timings = []
            original_run = module.run

            def measured(command, **kwargs):
                command = list(command)
                if '-filter_complex' in command:
                    kwargs['timeout'] = budget
                    stage = 'encode'
                elif '-show_frames' in command:
                    stage = 'frame_validation'
                elif 'null' in command:
                    stage = 'decode_validation'
                else:
                    stage = 'other'
                start = time.monotonic()
                try:
                    return original_run(command, **kwargs)
                finally:
                    timings.append(dict(stage=stage, seconds=round(time.monotonic()-start, 4)))

            start = time.monotonic()
            try:
                with patch.object(module, 'run', measured):
                    artifact = renderer.render(conn, 1, snap, source)
                status, error = artifact['state'], None
            except Exception as exc:
                status, error = 'FAILED', getattr(exc, 'code', type(exc).__name__)
            results.append(dict(threads=1, state=status, error_code=error,
                total_seconds=round(time.monotonic()-start, 4), timings=timings,
                source_sha256=source_hash, render_spec_hash=snap['render_spec_hash'],
                renderer_identity=renderer.identity,
                source_bytes=source.stat().st_size, frames=723, resolution=[1080,1920],
                fps='30/1', duration=24.1, encode_timeout=budget))
            conn.close()
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(results, indent=2), encoding='utf-8')
            print(json.dumps(results[-1]), flush=True)


if __name__ == '__main__':
    main()
