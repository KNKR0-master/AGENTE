"""Controlled synthetic-only encoder benchmark. No operational files or accounts."""
import argparse
import ctypes
from ctypes import wintypes
import hashlib
import json
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import threading
import time
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from src.publisher import headline_renderer as renderer_module
from src.publisher.headline_layout import filters
from src.publisher.headline_tools import resolve_ffmpeg, run, sha256, probe
from src.publisher.headlines import DEFAULT_SPEC, canonical, render_hash

ROOT = Path(__file__).resolve().parents[1]
FONT = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts/arialbd.ttf'


def process_cpu(process):
    """Owned child CPU time, including after exit while its HANDLE is retained."""
    values = [wintypes.FILETIME() for _ in range(4)]
    function = ctypes.windll.kernel32.GetProcessTimes
    function.argtypes = [wintypes.HANDLE] + [ctypes.POINTER(wintypes.FILETIME)] * 4
    function.restype = wintypes.BOOL
    if not function(int(process._handle), *(ctypes.byref(v) for v in values)):
        raise ctypes.WinError()
    return sum(((v.dwHighDateTime << 32) | v.dwLowDateTime) / 10000000 for v in values[2:])


def make_snapshot():
    value = dict(render_spec_version=1, enabled=True, required=False, text='OFERTA DO DIA',
        preset=dict(id=1, version=1, name='Synthetic performance', spec={**DEFAULT_SPEC,
            'font_ref': 'fixture', 'font_sha256': sha256(FONT), 'font_weight': 700,
            'font_size_ratio': .034, 'text_color': '#FFE500', 'background_color': None,
            'stroke_color': None, 'shadow_color': None}), provenance='DRAFT_OVERRIDE')
    value['render_spec_hash'] = render_hash(value)
    return value


def generate(ffmpeg, path, seconds, textured):
    graph = ('noise=alls=2:allf=t:all_seed=12345,' if textured else '')
    graph += 'settb=1/15360'
    if textured:
        graph += ',setpts=N*512+if(gte(N\\,12)\\,215\\,0)+if(gte(N\\,24)\\,61\\,0)'
    run([ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=1080x1920:rate=30',
         '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', str(seconds),
         '-vf', graph, '-vsync', '0', '-enc_time_base:v', '1/15360', '-c:v', 'libx264',
         '-preset', 'ultrafast', '-crf', '18', '-threads', '4', '-pix_fmt', 'yuv420p',
         '-c:a', 'aac', '-ac', '2', path], timeout=120)


def audio_signature(ffmpeg, ffprobe, path):
    digest = run([ffmpeg, '-v', 'error', '-i', path, '-map', '0:a:0',
                  '-c:a', 'copy', '-f', 'hash', '-hash', 'sha256', '-'], timeout=120).strip()
    packets = json.loads(run([ffprobe, '-v', 'error', '-select_streams', 'a:0', '-show_packets',
        '-show_entries', 'packet=pts,dts,duration,size', '-of', 'json', path], timeout=120))['packets']
    return dict(sha256=digest, packets=packets)


def reference(renderer, artifact, source, destination):
    workspace = Path(artifact['local_path']).parent
    plan = json.loads(artifact['layout_json'])
    media = probe(renderer.ffprobe, source)
    graph = filters(plan, workspace)
    run([renderer.ffmpeg, '-v', 'error', '-y', '-i', source, '-filter_complex_threads', '1',
         '-filter_complex', graph, '-map', '[out]', '-an', '-vsync', '0',
         '-enc_time_base:v', media['video']['time_base'], '-threads', '2', '-c:v', 'ffv1',
         '-level', '3', '-pix_fmt', 'yuv420p', '-f', 'nut', destination], cwd=workspace, timeout=120)


def quality(ffmpeg, reference_path, output, directory):
    directory.mkdir(parents=True, exist_ok=True)
    graph = ("[0:v]settb=AVTB,setpts=N/(30*TB),split[r1][r2];"
             "[1:v]settb=AVTB,setpts=N/(30*TB),split[d1][d2];"
             "[d1][r1]ssim=stats_file=ssim.log[s];[d2][r2]psnr=stats_file=psnr.log[p]")
    p = subprocess.run([str(ffmpeg), '-hide_banner', '-v', 'info', '-i', str(reference_path),
        '-i', str(output), '-filter_complex_threads', '1', '-filter_complex', graph,
        '-map', '[s]', '-map', '[p]', '-an', '-f', 'null', '-'],
        cwd=directory, capture_output=True, text=True, timeout=120)
    if p.returncode:
        raise RuntimeError('QUALITY_PROCESS_FAILED: ' + p.stderr[-1000:])
    ssim = re.findall(r'SSIM .*All:([\d.]+)', p.stderr)
    psnr = re.findall(r'PSNR .*average:([\d.]+|inf)', p.stderr)
    if not ssim or not psnr:
        raise RuntimeError('QUALITY_METRICS_MISSING')
    return dict(ssim=float(ssim[-1]), psnr_db=float(psnr[-1]),
        compared_frames=len((directory/'ssim.log').read_text().splitlines()))


def trial(base, source, threads, name, snapshot):
    renderer = renderer_module.HeadlineRenderer(ROOT, source.parent, base/'artifacts'/name,
        {'fixture': FONT}, encode_timeout=600)
    # Explicit identity for each experimental command, even before implementation.
    renderer.identity['encoder_threads'] = threads
    renderer.version = hashlib.sha256(canonical(renderer.identity).encode()).hexdigest()
    conn = sqlite3.connect(':memory:'); conn.row_factory = sqlite3.Row
    renderer_module.init_artifacts(conn)
    stages, processes, hashes = [], [], []
    renderer.stage_observer = stages.append
    original_popen, original_run, original_hash = subprocess.Popen, renderer_module.run, renderer_module.sha256

    def tracked_popen(command, *args, **kwargs):
        process = original_popen(command, *args, **kwargs)
        stage = ('encode' if '-filter_complex' in command else 'frame_probe' if '-show_frames' in command
                 else 'metadata' if '-show_streams' in command else 'decode_validation' if 'null' in command else 'other')
        record = dict(stage=stage, process=process, started=time.monotonic(), samples=[], stop=threading.Event())
        def sample():
            previous, last = 0, record['started']
            while not record['stop'].wait(.05):
                now, cpu = time.monotonic(), process_cpu(process)
                record['samples'].append((cpu-previous)/(now-last)*100)
                previous, last = cpu, now
        record['sampler'] = threading.Thread(target=sample, daemon=True)
        record['sampler'].start(); processes.append(record)
        return process

    def experimental_run(command, **kwargs):
        command = list(command)
        if '-filter_complex' in command:
            option = '-threads:v' if '-threads:v' in command else '-threads'
            command[command.index(option)+1] = str(threads)
        return original_run(command, **kwargs)

    def measured_hash(path):
        wall, cpu = time.monotonic(), time.process_time()
        result = original_hash(path)
        hashes.append(dict(file=Path(path).name, seconds=time.monotonic()-wall,
                           cpu_seconds=time.process_time()-cpu))
        return result

    started = time.monotonic()
    try:
        with patch.object(subprocess, 'Popen', tracked_popen), patch.object(renderer_module, 'run', experimental_run), patch.object(renderer_module, 'sha256', measured_hash):
            artifact = renderer.render(conn, 1, snapshot, source)
        total = time.monotonic()-started
        renderer.stage_observer = None
    finally:
        for item in processes:
            item['stop'].set(); item['sampler'].join(1)
    metrics = []
    for item in processes:
        cpu = process_cpu(item['process'])
        metrics.append(dict(stage=item['stage'], cpu_seconds=cpu,
            peak_process_cpu_percent=max(item['samples'], default=0)))
    encode = next(s['seconds'] for s in stages if s['stage']=='encode')
    encode_cpu = next(p['cpu_seconds'] for p in metrics if p['stage']=='encode')
    media = json.loads(artifact['probe_json'])
    timeline = renderer.presentation_timeline(source, probe(renderer.ffprobe, source)['video']['time_base'])
    signature = audio_signature(renderer.ffmpeg, renderer.ffprobe, Path(artifact['local_path']))
    source_signature = audio_signature(renderer.ffmpeg, renderer.ffprobe, source)
    source_media = probe(renderer.ffprobe, source)
    av_delta = lambda m: float(m['video'].get('start_time', 0))-float(m['audio'][0].get('start_time', 0))
    result = dict(name=name, threads=threads, state=artifact['state'], total_seconds=total,
        encode_seconds=encode, processing_fps=len(timeline.times)/encode,
        encode_cpu_seconds=encode_cpu, encode_mean_process_cpu_percent=encode_cpu/encode*100,
        encode_mean_machine_cpu_percent=encode_cpu/encode*100/(os.cpu_count() or 1),
        stages=stages, processes=metrics, hashing=hashes,
        source_sha256=artifact['source_sha256'], render_spec_hash=artifact['render_spec_hash'],
        renderer_identity=renderer.identity, renderer_version=renderer.version,
        output_sha256=artifact['output_sha256'], output_bytes=Path(artifact['local_path']).stat().st_size,
        frame_count=len(timeline.times), vfr=len(set(b-a for a,b in zip(timeline.times,timeline.times[1:])))>1,
        full_validation_passed=True, audio_payload_identical=signature['sha256']==source_signature['sha256'],
        audio_packet_timestamps_identical=signature['packets']==source_signature['packets'],
        av_start_delta_error=abs(av_delta(media)-av_delta(source_media)),
        output_probe=media, artifact_path=artifact['local_path'], source_path=str(source))
    conn.close()
    return renderer, artifact, result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--directory', type=Path, required=True)
    parser.add_argument('--phase', choices=['short','long'], default='short')
    parser.add_argument('--winner', type=int, choices=[2,4])
    args = parser.parse_args()
    base = args.directory.resolve()
    import tempfile
    if not base.is_relative_to(Path(tempfile.gettempdir()).resolve()):
        raise ValueError('Synthetic artifact directory must be under OS TEMP')
    base.mkdir(parents=True, exist_ok=True); inputs=base/'inputs'; inputs.mkdir(exist_ok=True)
    if args.phase=='long' and args.winner is None:
        raise ValueError('Select a validated short-sample winner first')
    cases = [('plain-cfr',False), ('textured-vfr',True)] if args.phase=='short' else [('textured-vfr-long',True)]
    results=[]; snapshot=make_snapshot(); ffmpeg=resolve_ffmpeg(ROOT)
    for case,textured in cases:
        source=inputs/(case+'.mp4')
        if not source.exists(): generate(ffmpeg,source,1.2 if args.phase=='short' else 6,textured)
        reference_path=base/(case+'-reference.nut')
        order=[1,2,4,4,2,1] if args.phase=='short' else [1,args.winner]
        for index,threads in enumerate(order):
            renderer,artifact,result=trial(base,source,threads,f'{case}-{index}-t{threads}',snapshot)
            if not reference_path.exists(): reference(renderer,artifact,source,reference_path)
            result['quality']=quality(ffmpeg,reference_path,Path(artifact['local_path']),base/'quality'/result['name'])
            results.append(result)
            (base/(args.phase+'-results.json')).write_text(json.dumps(results,indent=2),encoding='utf-8')
            print(json.dumps({k:result[k] for k in ('name','threads','encode_seconds','total_seconds','processing_fps','encode_mean_machine_cpu_percent','output_bytes','audio_payload_identical','audio_packet_timestamps_identical','av_start_delta_error','frame_count','vfr','quality')}),flush=True)


if __name__=='__main__': main()
