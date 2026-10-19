import copy
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest.mock import patch

from src.publisher.headlines import DEFAULT_SPEC, render_hash
from src.publisher.headline_layout import layout, filters
from src.publisher.headline_renderer import HeadlineRenderer, init_artifacts, validate_output
from src.publisher.headline_tools import RenderError, capabilities, probe, resolve_ffmpeg, run, sha256

ROOT = Path(__file__).resolve().parents[1]
FONT = Path(os.environ.get('WINDIR', 'C:/Windows')) / 'Fonts' / 'arial.ttf'


def snapshot(text='OFERTA DO DIA', **changes):
    result = dict(render_spec_version=1, enabled=True, required=False, text=text,
                  preset={'id': 1, 'version': 1, 'name': 'Fixture', 'spec': {
                      **DEFAULT_SPEC, 'font_ref': 'fixture', 'font_sha256': sha256(FONT),
                      'font_size_ratio': .035, **changes}}, provenance='DRAFT_OVERRIDE')
    result['render_spec_hash'] = render_hash(result)
    return result


class HeadlineRendererTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='publisher-headline-h2-')
        cls.root = Path(cls.temp.name)
        cls.inputs = cls.root / 'local sources with spaces'
        cls.inputs.mkdir()
        cls.renderer = HeadlineRenderer(ROOT, cls.inputs, cls.root / 'artifacts', {'fixture': FONT})
        cls.silent = cls.inputs / 'portrait.mp4'
        cls.audio = cls.inputs / 'audio.mp4'
        cls.short = cls.inputs / 'short.mp4'
        cls.rotated = cls.inputs / 'rotated.mp4'
        cls.vfr = cls.inputs / 'variable.mp4'
        base = [cls.renderer.ffmpeg, '-v', 'error', '-y', '-f', 'lavfi', '-i', 'testsrc2=size=360x640:rate=24']
        run([*base, '-t', '1.5', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', cls.silent])
        run([*base, '-f', 'lavfi', '-i', 'sine=frequency=440:sample_rate=48000', '-t', '1.5',
             '-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-c:a', 'aac', cls.audio])
        run([*base, '-t', '0.125', '-c:v', 'libx264', '-pix_fmt', 'yuv420p', cls.short])
        run([cls.renderer.ffmpeg, '-v', 'error', '-y', '-i', cls.silent, '-c', 'copy', '-metadata:s:v:0', 'rotate=90', cls.rotated])
        run([*base, '-t', '1.5', '-vf', "select='if(lt(n,12),1,not(mod(n,2)))'", '-vsync', '0', '-c:v', 'libx264', cls.vfr])

    @classmethod
    def tearDownClass(cls):
        cls.temp.cleanup()

    def setUp(self):
        self.conn = sqlite3.connect(':memory:')
        self.conn.row_factory = sqlite3.Row
        init_artifacts(self.conn)
        self.addCleanup(self.conn.close)

    def render(self, source=None, headline=None, **kwargs):
        return self.renderer.render(self.conn, 1, headline or snapshot(), source or self.silent, **kwargs)

    def test_real_silent_video_atomic_ready_and_cache(self):
        original = sha256(self.silent)
        result = self.render()
        self.assertEqual(result['state'], 'READY')
        self.assertFalse(Path(result['local_path']).with_name('output.partial').exists())
        self.assertEqual(sha256(result['local_path']), result['output_sha256'])
        self.assertEqual(sha256(self.silent), original)
        reused = self.render()
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['id'], result['id'])

    def test_encode_deadline_separate_from_validation_and_timeout_recoverable(self):
        events = []
        renderer = HeadlineRenderer(ROOT, self.inputs, self.root/'budget-artifacts',
            {'fixture': FONT}, timeout=30, encode_timeout=.001)
        renderer.stage_observer = events.append
        original = sha256(self.silent)
        with self.assertRaisesRegex(RenderError, 'HEADLINE_PROCESS_TIMEOUT'):
            renderer.render(self.conn, 2, snapshot(), self.silent)
        failed = dict(self.conn.execute('SELECT * FROM publisher_derived_media_artifacts').fetchone())
        self.assertEqual(failed['state'], 'FAILED')
        self.assertIsNone(failed['local_path'])
        self.assertTrue(any(e['stage']=='encode' and e['error_code']=='HEADLINE_PROCESS_TIMEOUT' for e in events))
        self.assertNotIn(str(self.silent), json.dumps(events))
        renderer.encode_timeout = 600
        calls = []
        def checked_run(args, **kwargs):
            calls.append((list(args), kwargs.get('timeout')))
            return run(args, **kwargs)
        with patch('src.publisher.headline_renderer.run', side_effect=checked_run):
            artifact = renderer.render(self.conn, 2, snapshot(), self.silent)
        self.assertEqual(artifact['state'], 'READY')
        self.assertEqual(sha256(self.silent), original)
        self.assertEqual(artifact['render_spec_hash'], snapshot()['render_spec_hash'])
        self.assertEqual(next(t for a,t in calls if '-filter_complex' in a), 600)
        self.assertEqual(next(t for a,t in calls if 'null' in a), 30)
        self.assertEqual(self.conn.execute("SELECT state FROM publisher_derived_media_artifacts WHERE id=?", (failed['id'],)).fetchone()[0], 'FAILED')
        # A process budget is not an output-identity change; validated cache stays usable.
        renderer.encode_timeout = .001
        self.assertTrue(renderer.render(self.conn, 2, snapshot(), self.silent)['reused'])

    def test_encode_budget_remains_bounded(self):
        for value in (0, -1, 601):
            with self.assertRaisesRegex(RenderError, 'HEADLINE_PROCESS_LIMIT_INVALID'):
                HeadlineRenderer(ROOT, self.inputs, self.root/'invalid-budget',
                    {'fixture': FONT}, encode_timeout=value)

    def test_video_parallelism_bounded_and_preview_filter_deadlines_preserved(self):
        with patch('src.publisher.headline_renderer.os.cpu_count', return_value=32):
            renderer = HeadlineRenderer(ROOT, self.inputs, self.root/'bounded-parallel', {'fixture': FONT})
        self.assertEqual(renderer.identity['encoder_threads'], 4)
        commands = []
        def checked_run(args, **kwargs):
            if '-filter_complex' in args: commands.append((list(args), kwargs['timeout']))
            return run(args, **kwargs)
        with patch('src.publisher.headline_renderer.run', side_effect=checked_run):
            renderer.render(self.conn, 3, snapshot(), self.short)
            renderer.render(self.conn, 3, snapshot(), self.short, kind='preview')
        video, preview = commands
        self.assertEqual(video[0][video[0].index('-threads:v')+1], '4')
        self.assertEqual(video[0][video[0].index('-filter_complex_threads')+1], '1')
        self.assertEqual(video[1], 600)
        self.assertNotIn('-threads:v', preview[0])
        self.assertEqual(preview[0][preview[0].index('-threads')+1], '1')
        self.assertEqual(preview[1], 120)
        with patch('src.publisher.headline_renderer.os.cpu_count', return_value=None):
            fallback = HeadlineRenderer(ROOT, self.inputs, self.root/'unknown-cpu', {'fixture': FONT})
        self.assertEqual(fallback.identity['encoder_threads'], 1)
        self.assertNotEqual(fallback.version, renderer.version)

    def test_parallelism_invalidates_legacy_cache_preserves_snapshot_source_and_old_artifact(self):
        import hashlib
        from src.publisher.headlines import canonical
        headline = snapshot(); before = copy.deepcopy(headline)
        with patch('src.publisher.headline_renderer.os.cpu_count', return_value=1):
            original = HeadlineRenderer(ROOT, self.inputs, self.root/'legacy-parallel', {'fixture': FONT})
        old = original.render(self.conn, 4, headline, self.silent)
        legacy_identity = {k:v for k,v in original.identity.items() if k!='encoder_threads'}
        legacy_version = hashlib.sha256(canonical(legacy_identity).encode()).hexdigest()
        legacy_key = hashlib.sha256(canonical([4, old['source_sha256'], old['render_spec_hash'], legacy_version, 'video']).encode()).hexdigest()
        with self.conn:
            self.conn.execute('UPDATE publisher_derived_media_artifacts SET cache_key=?,renderer_version=? WHERE id=?',
                (legacy_key, legacy_version, old['id']))
        with patch('src.publisher.headline_renderer.os.cpu_count', return_value=4):
            optimized = HeadlineRenderer(ROOT, self.inputs, self.root/'legacy-parallel', {'fixture': FONT})
        new = optimized.render(self.conn, 4, headline, self.silent)
        self.assertFalse(new['reused'])
        self.assertNotEqual(new['id'], old['id'])
        self.assertNotEqual(new['renderer_version'], legacy_version)
        self.assertEqual(new['source_sha256'], old['source_sha256'])
        self.assertEqual(new['render_spec_hash'], old['render_spec_hash'])
        self.assertEqual(new['layout_json'], old['layout_json'])
        self.assertEqual(headline, before)
        self.assertEqual(sha256(self.silent), old['source_sha256'])
        self.assertEqual(sha256(old['local_path']), old['output_sha256'])
        self.assertEqual(self.conn.execute('SELECT state FROM publisher_derived_media_artifacts WHERE id=?', (old['id'],)).fetchone()[0], 'READY')
        self.assertTrue(optimized.render(self.conn, 4, headline, self.silent)['reused'])

    def test_real_audio_preserved(self):
        result = self.render(self.audio)
        self.assertEqual(len(json.loads(result['probe_json'])['audio']), 1)

    def test_real_short_preview(self):
        result = self.render(self.short, kind='preview')
        self.assertEqual(result['state'], 'READY')

    def test_real_rotation(self):
        result = self.render(self.rotated)
        self.assertEqual(json.loads(result['probe_json'])['display'], [640, 360])

    def test_real_vfr_preserved(self):
        result = self.render(self.vfr)
        self.assertEqual(result['state'], 'READY')
        stamps = self.renderer.timestamps(self.vfr)
        self.assertGreater(len({round(b-a, 4) for a,b in zip(stamps, stamps[1:])}), 1)

    def test_real_text_matrix(self):
        for text in ('OFERTA DO DIA', 'R$ 49,90 \u2014 s\u00f3 hoje!', '30% OFF',
                     'Conforto, pre\u00e7o e qualidade', 'A\u00e7\u00e3o\nPre\u00e7o bom', "'oferta' \"boa\" : ; [] \\ 30%"):
            with self.subTest(text=text):
                self.assertEqual(self.render(headline=snapshot(text), kind='preview')['state'], 'READY')

    def test_preview_and_video_exact_layout(self):
        video = self.render()
        preview = self.render(kind='preview')
        self.assertEqual(video['layout_json'], preview['layout_json'])
        self.assertNotEqual(video['cache_key'], preview['cache_key'])
        self.assertTrue(self.render(kind='preview')['reused'])

    def test_background_stroke_blurred_shadow(self):
        result = self.render(headline=snapshot(background_color='#123456', stroke_color='#000000', shadow_color='#222222'))
        self.assertEqual(result['state'], 'READY')

    def test_unsupported_emoji_fail_closed(self):
        with self.assertRaisesRegex(RenderError, 'UNSUPPORTED_GLYPH'):
            self.render(headline=snapshot('\U0001fae0'))
        self.assertEqual(self.conn.execute('SELECT state FROM publisher_derived_media_artifacts').fetchone()[0], 'FAILED')

    def test_missing_font(self):
        with patch.dict(self.renderer.fonts, {'fixture': str(self.root / 'absent.ttf')}):
            with self.assertRaisesRegex(RenderError, 'FONT_UNAVAILABLE'):
                self.render()

    def test_font_hash_mismatch(self):
        with self.assertRaisesRegex(RenderError, 'FONT_UNAVAILABLE'):
            self.render(headline=snapshot(font_sha256='0'*64))

    def test_font_face_invalid(self):
        with self.assertRaisesRegex(RenderError, 'FACE_INVALID'):
            self.render(headline=snapshot(font_face_index=5))

    def test_disabled_no_artifact(self):
        value = snapshot()
        value['enabled'] = False
        value['render_spec_hash'] = render_hash(value)
        result = self.render(headline=value)
        self.assertEqual(result['state'], 'DERIVED_ARTIFACT_NOT_REQUIRED')
        self.assertEqual(self.conn.execute('SELECT count(*) FROM publisher_derived_media_artifacts').fetchone()[0], 0)

    def test_max_lines_and_overflow(self):
        for value in (snapshot('Palavra '*40, max_lines=1), snapshot('A'*200), snapshot(y=.95)):
            with self.assertRaises(RenderError):
                self.render(headline=value)

    def test_valid_wrap(self):
        result = self.render(headline=snapshot('Conforto e qualidade para o seu dia', max_width=.5, max_lines=5), kind='preview')
        self.assertGreater(len(json.loads(result['layout_json'])['lines']), 1)

    def test_text_not_interpolated_in_graph(self):
        with tempfile.TemporaryDirectory() as directory:
            value = snapshot("50%: ';[]\\\npre\u00e7o")
            plan = layout(value['text'], value['preset']['spec'], [360,640], {'fixture':FONT}, directory)
            graph = filters(plan, directory)
            self.assertNotIn(value['text'], graph)
            self.assertIn('expansion=none', graph)
            self.assertIn('textfile=', graph)
            self.assertEqual((Path(directory)/'line0.txt').read_text(encoding='utf-8'), "50%: ';[]\\")

    def test_invalid_cache_hash_rejected_history_retained(self):
        old = self.render()
        Path(old['local_path']).write_bytes(b'corrupt')
        new = self.render()
        self.assertNotEqual(old['id'], new['id'])
        self.assertEqual(self.conn.execute('SELECT state FROM publisher_derived_media_artifacts WHERE id=?',(old['id'],)).fetchone()[0], 'FAILED')

    def test_missing_cache_rerenders(self):
        old = self.render()
        Path(old['local_path']).unlink()
        self.assertNotEqual(self.render()['id'], old['id'])

    def test_changed_spec_new_artifact(self):
        old = self.render(kind='preview')
        new = self.render(headline=snapshot('OUTRA OFERTA'),kind='preview')
        self.assertNotEqual(old['cache_key'],new['cache_key'])

    def test_process_failures_never_ready(self):
        for code in ('HEADLINE_PROCESS_TIMEOUT','HEADLINE_PROCESS_EXIT_ERROR','HEADLINE_OUTPUT_INVALID'):
            with patch('src.publisher.headline_renderer.run', side_effect=RenderError(code)):
                with self.assertRaisesRegex(RenderError,code):
                    self.render()
        self.assertEqual(self.conn.execute("SELECT count(*) FROM publisher_derived_media_artifacts WHERE state='READY'").fetchone()[0],0)

    def test_interrupted_generating_not_reused(self):
        old = self.render()
        self.conn.execute("UPDATE publisher_derived_media_artifacts SET state='GENERATING'")
        self.conn.commit()
        new = self.render()
        self.assertNotEqual(old['id'],new['id'])

    def test_wrong_output_contract(self):
        source = probe(self.renderer.ffprobe,self.audio)
        for update in ({'codec_name':'mpeg4'},{'pix_fmt':'rgb24'}):
            output=copy.deepcopy(source)
            output['video'].update(update)
            with self.assertRaises(RenderError): validate_output(source,output,'video')
        for update in ({'duration':50},{'audio':[]},{'display':[100,200]}):
            output=copy.deepcopy(source);output.update(update)
            with self.assertRaises(RenderError): validate_output(source,output,'video')

    def test_invalid_source(self):
        bad=self.inputs/'bad.mp4';bad.write_bytes(b'not mp4')
        with self.assertRaises(RenderError): self.render(bad)

    def test_path_traversal(self):
        with self.assertRaisesRegex(RenderError,'PATH_REJECTED'):
            self.render(self.inputs/'..'/'secret.mp4')

    def test_same_spec_forced_semantic_determinism(self):
        first=self.render();second=self.render(force=True)
        self.assertEqual(first['cache_key'],second['cache_key'])
        self.assertEqual(first['layout_json'],second['layout_json'])

    def test_resolver_explicit_invalid_no_fallback(self):
        with self.assertRaises(RenderError): resolve_ffmpeg(ROOT,self.root/'no.exe')

    def test_capability_cached(self):
        with patch('src.publisher.headline_tools.run',side_effect=AssertionError('reprobe')):
            self.assertIn('version',capabilities(self.renderer.ffmpeg))

    def test_valid_collection_face(self):
        from fontTools.ttLib import TTFont, TTCollection
        collection = TTCollection()
        collection.fonts = [TTFont(FONT), TTFont(FONT)]
        path = self.inputs / 'collection.ttc'
        collection.save(path)
        for font in collection.fonts: font.close()
        with patch.dict(self.renderer.fonts, {'fixture': path}):
            result = self.render(headline=snapshot(font_sha256=sha256(path),font_face_index=1),kind='preview')
        self.assertEqual(result['state'],'READY')

    def test_single_frame_preview(self):
        source = self.inputs / 'one-frame.mp4'
        run([self.renderer.ffmpeg,'-v','error','-y','-i',self.silent,'-frames:v','1','-c:v','libx264',source])
        self.assertEqual(self.render(source,kind='preview')['state'],'READY')

    def test_non_aac_audio_encoded_without_dropping_track(self):
        source = self.inputs / 'pcm.mkv'
        run([self.renderer.ffmpeg,'-v','error','-y','-i',self.audio,'-c:v','copy','-c:a','pcm_s16le',source])
        result=self.render(source)
        self.assertEqual(json.loads(result['probe_json'])['audio'][0]['codec_name'],'aac')

    def test_invalid_probe_ready_not_reused_even_when_hash_matches(self):
        old=self.render()
        path=Path(old['local_path']);path.write_bytes(b'not a video')
        self.conn.execute('UPDATE publisher_derived_media_artifacts SET output_sha256=? WHERE id=?',(sha256(path),old['id']))
        self.conn.commit()
        self.assertNotEqual(self.render()['id'],old['id'])

    def test_partial_interrupted_render_never_ready(self):
        def interrupted(args, **kwargs):
            Path(args[-1]).write_bytes(b'partial')
            raise RenderError('HEADLINE_PROCESS_TIMEOUT')
        with patch('src.publisher.headline_renderer.run',side_effect=interrupted):
            with self.assertRaises(RenderError):self.render()
        row=dict(self.conn.execute('SELECT * FROM publisher_derived_media_artifacts').fetchone())
        self.assertEqual(row['state'],'FAILED')
        self.assertTrue((self.renderer.artifact_root/row['id']/'output.partial').exists())
        self.assertIsNone(row['local_path'])
        self.assertNotEqual(self.render()['id'],row['id'])

    def test_output_validation_failure_never_ready(self):
        with patch.object(self.renderer,'validate_file',side_effect=RenderError('HEADLINE_OUTPUT_INVALID')):
            with self.assertRaises(RenderError):self.render()
        self.assertEqual(self.conn.execute('SELECT state FROM publisher_derived_media_artifacts').fetchone()[0],'FAILED')

    def test_source_identity_changes_fail_closed(self):
        reads=0
        def identity(path):
            nonlocal reads
            if Path(path)==self.silent:
                reads+=1
                if reads>1:return '0'*64
            return sha256(path)
        with patch('src.publisher.headline_renderer.sha256',side_effect=identity):
            with self.assertRaisesRegex(RenderError,'SOURCE_CHANGED'):self.render()

    def test_resolver_env_and_config_precedence(self):
        config_root=self.root/'resolver';(config_root/'config').mkdir(parents=True,exist_ok=True)
        config=config_root/'config'/'media_tools.json'
        config.write_text(json.dumps({'ffmpeg_path':str(self.renderer.ffmpeg)}),encoding='utf-8')
        with patch.dict(os.environ,{'FFMPEG_PATH':str(self.root/'bad.exe')}):
            self.assertEqual(resolve_ffmpeg(config_root),self.renderer.ffmpeg)
        config.unlink()
        with patch.dict(os.environ,{'FFMPEG_PATH':str(self.renderer.ffmpeg)}):
            self.assertEqual(resolve_ffmpeg(config_root),self.renderer.ffmpeg)

    def test_capability_missing_fails_closed(self):
        with patch('src.publisher.headline_tools.sha256',return_value='different-binary'), patch('src.publisher.headline_tools.run',return_value='nothing'):
            with self.assertRaisesRegex(RenderError,'CAPABILITY_MISSING'):capabilities(self.renderer.ffmpeg)


class BoundedProcessTests(unittest.TestCase):
    def test_timeout(self):
        with self.assertRaisesRegex(RenderError,'TIMEOUT'):
            run([sys.executable,'-c','import time; time.sleep(10)'],timeout=.05)

    def test_nonzero(self):
        with self.assertRaisesRegex(RenderError,'EXIT_ERROR'):
            run([sys.executable,'-c','raise SystemExit(7)'])

    def test_bounded_output(self):
        with self.assertRaisesRegex(RenderError,'OUTPUT_LIMIT'):
            run([sys.executable,'-c','print("x"*100000)'],limit=100)

    def test_child_killed(self):
        with self.assertRaisesRegex(RenderError,'EXIT_ERROR'):
            run([sys.executable,'-c','import os; os._exit(9)'])
