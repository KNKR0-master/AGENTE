import copy
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch

from src.publisher import headlines, headline_workflow as workflow
from src.publisher.headline_layout import layout, filters
from src.publisher.headline_renderer import HeadlineRenderer, init_artifacts
from src.publisher.headline_previews import HeadlinePreviewCache, comparison_request
from src.publisher.headline_tools import run, sha256
from tests.test_publisher_headline_renderer import ROOT, FONT


FONT_SPEC = dict(font_ref='classic', font_sha256=sha256(FONT), font_face_index=0, font_weight=400)


def headline(style='YELLOW', text='KIT 4 BODY SPLASH\nT\u00c1 MUITO BARATO!', **overrides):
    spec = {**workflow.classic_spec(FONT_SPEC, style, 'STANDARD'), **overrides}
    snapshot = dict(render_spec_version=1, enabled=True, required=True, text=text,
                    preset=dict(id=1, version=2, name=workflow.CALIBRATED_LABELS[style], spec=spec),
                    size='STANDARD', provenance='PRODUCT_DEFAULT')
    snapshot['render_spec_hash'] = headlines.render_hash(snapshot)
    return snapshot


class CalibratedLayoutTests(unittest.TestCase):
    def plan(self, style, display=(720, 1280), text='KIT 4 BODY SPLASH\nCURTO'):
        with tempfile.TemporaryDirectory() as directory:
            plan = layout(text, headline(style, text)['preset']['spec'], display, {'classic': FONT}, directory)
            graph = filters(plan, directory)
        return plan, graph

    def test_each_unequal_line_and_block_centered(self):
        plan, graph = self.plan('YELLOW')
        self.assertNotEqual(plan['widths'][0], plan['widths'][1])
        for x, y, width, height in [plan['box'], *plan['line_boxes']]:
            self.assertEqual(x + width / 2, 360)
        self.assertEqual(graph.count('x=360-text_w/2'), 2)
        self.assertEqual(plan['line_boxes'][1][1] - plan['line_boxes'][0][1], plan['line_height'])

    def test_symmetric_safe_top_720_1080_all_three_styles(self):
        for display in ((720, 1280), (1080, 1920)):
            for style in ('YELLOW', 'OUTLINE', 'BLACK_BACKGROUND'):
                with self.subTest(display=display, style=style):
                    plan, _ = self.plan(style, display)
                    x, y, width, height = plan['box']
                    extent = max(plan['padding'], plan['stroke']) + 2
                    self.assertGreaterEqual(x - extent, display[0] * .14)
                    self.assertLessEqual(x + width + extent, display[0] * .86)
                    self.assertGreaterEqual(y - extent, display[1] * .06)
                    self.assertLessEqual(y + height + extent, display[1] * .32)

    def test_yellow_and_white_outline_no_background_or_shadow(self):
        for style in ('YELLOW', 'OUTLINE'):
            spec = headline(style)['preset']['spec']
            self.assertEqual(spec['stroke_color'], '#000000')
            self.assertIsNone(spec['background_color'])
            self.assertIsNone(spec['shadow_color'])

    def test_rounded_black_background_is_per_line(self):
        plan, graph = self.plan('BLACK_BACKGROUND')
        self.assertTrue(plan['spec']['background_per_line'])
        self.assertGreater(plan['spec']['background_radius_ratio'], 0)
        self.assertEqual(graph.count('geq='), 2)
        self.assertNotEqual(plan['line_boxes'][0][2], plan['line_boxes'][1][2])

    def test_pixel_style_changes_hash(self):
        original = headline('BLACK_BACKGROUND')
        for field, value in (('background_radius_ratio', .005), ('background_per_line', False), ('x', .49)):
            changed = copy.deepcopy(original); changed['preset']['spec'][field] = value
            self.assertNotEqual(headlines.render_hash(changed), original['render_spec_hash'])

    def test_background_only_fields_do_not_affect_plain_text_hash(self):
        original = headline('YELLOW')
        changed = copy.deepcopy(original)
        changed['preset']['spec'].update(background_radius_ratio=.005, background_per_line=True)
        self.assertEqual(headlines.render_hash(changed), original['render_spec_hash'])


class CalibratedVersionTests(unittest.TestCase):
    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close)
        self.conn.executescript('CREATE TABLE publisher_products(product_id INTEGER PRIMARY KEY);'
                                'CREATE TABLE publisher_drafts(id INTEGER PRIMARY KEY);'
                                'CREATE TABLE publisher_migrations(name TEXT PRIMARY KEY,created_at TEXT);')
        headlines.init_headlines(self.conn)
        self.conn.commit()

    def initialize(self):
        with patch('src.publisher.headline_configuration.machine_readiness', return_value={'ready':True, 'classic_font':FONT_SPEC}):
            return workflow.initialize_styles(self.conn, ROOT)

    def test_explicit_upgrade_preserves_old_version_and_snapshot(self):
        old = headline(); old['preset']['spec'] = workflow.classic_spec(FONT_SPEC, 'YELLOW', 'STANDARD', legacy=True)
        created = headlines.save_preset(self.conn, {'name':'Classic · Amarelo · Padrão', 'spec':old['preset']['spec']})
        self.conn.execute('INSERT INTO publisher_headline_style_variants VALUES (?,?,?)', ('YELLOW','STANDARD',created['id']))
        old['preset']['id'] = created['id']; old['preset']['version'] = 1
        old['render_spec_hash'] = headlines.render_hash(old)
        self.conn.execute('INSERT INTO publisher_drafts(id,headline_snapshot_json) VALUES (44,?)', (headlines.canonical(old),))
        self.conn.commit()
        before = self.conn.execute('SELECT spec_json FROM publisher_headline_preset_versions WHERE preset_id=? AND version=1', (created['id'],)).fetchone()[0]
        self.initialize()
        self.assertEqual(headlines.get_preset(self.conn, created['id'])['version'], 2)
        self.assertEqual(self.conn.execute('SELECT spec_json FROM publisher_headline_preset_versions WHERE preset_id=? AND version=1', (created['id'],)).fetchone()[0], before)
        self.assertEqual(json.loads(self.conn.execute('SELECT headline_snapshot_json FROM publisher_drafts WHERE id=44').fetchone()[0]), old)
        self.initialize(); self.assertEqual(headlines.get_preset(self.conn, created['id'])['version'], 2)

    def test_human_edited_preset_not_overwritten(self):
        created = headlines.save_preset(self.conn, {'name':'Meu estilo', 'spec':headline()['preset']['spec']})
        self.conn.execute('INSERT INTO publisher_headline_style_variants VALUES (?,?,?)', ('YELLOW','STANDARD',created['id']))
        self.conn.commit()
        before = headlines.get_preset(self.conn, created['id']); self.initialize()
        self.assertEqual(headlines.get_preset(self.conn, created['id']), before)

    def test_keep_frozen_snapshot_uses_captured_version(self):
        self.initialize()
        preset_id = next(s['preset_id'] for s in workflow.style_catalog(self.conn) if s['style_key']=='YELLOW' and s['size_key']=='STANDARD')
        old = headline(); old['preset']=headlines.get_preset(self.conn,preset_id)
        old['render_spec_hash']=headlines.render_hash(old)
        self.conn.execute('ALTER TABLE publisher_drafts ADD COLUMN temp_path TEXT')
        self.conn.execute('ALTER TABLE publisher_drafts ADD COLUMN video_path TEXT')
        with tempfile.NamedTemporaryFile() as source:
            self.conn.execute('INSERT INTO publisher_drafts(id,headline_snapshot_json,temp_path) VALUES (44,?,?)',
                              (headlines.canonical(old),source.name)); self.conn.commit()
            headlines.save_preset(self.conn,{'id':preset_id,'expected_version':1,'name':'Novo','spec':{**old['preset']['spec'],'text_color':'#FFFFFF'}})
            result,_=comparison_request(self.conn,{'context':{'draft_id':44},'selection':{'size':'STANDARD'}})
            self.assertEqual(result,old)
            result,_=comparison_request(self.conn,{'context':{'draft_id':44},'selection':{'preset_id':preset_id}})
            self.assertEqual(result['preset']['version'],2)
            self.assertEqual(json.loads(self.conn.execute('SELECT headline_snapshot_json FROM publisher_drafts WHERE id=44').fetchone()[0]),old)


class CalibratedRenderTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp = tempfile.TemporaryDirectory(prefix='publisher-style-calibration-')
        cls.root = Path(cls.temp.name); cls.inputs = cls.root/'inputs'; cls.inputs.mkdir()
        cls.renderer = HeadlineRenderer(ROOT, cls.inputs, cls.root/'artifacts', {'classic': FONT})
        cls.sources = {}
        for width, height in ((720, 1280), (1080, 1920)):
            source = cls.inputs/f'{width}.mp4'
            run([cls.renderer.ffmpeg, '-nostdin', '-v', 'error', '-y', '-f', 'lavfi', '-i',
                 f'testsrc2=size={width}x{height}:rate=24', '-t', '0.125', '-threads','1', '-c:v','libx264', source])
            cls.sources[width] = source

    @classmethod
    def tearDownClass(cls): cls.temp.cleanup()

    def setUp(self):
        self.conn = sqlite3.connect(':memory:'); self.conn.row_factory = sqlite3.Row
        self.addCleanup(self.conn.close); init_artifacts(self.conn)

    def test_three_styles_real_720_1080_preview_video_layout_and_cadence(self):
        for width, source in self.sources.items():
            for style in ('YELLOW','OUTLINE','BLACK_BACKGROUND'):
                with self.subTest(width=width, style=style):
                    video = self.renderer.render(self.conn, 1, headline(style), source)
                    preview = self.renderer.render(self.conn, 1, headline(style), source, kind='preview')
                    self.assertEqual(video['state'], 'READY'); self.assertEqual(preview['state'], 'READY')
                    self.assertEqual(video['layout_json'], preview['layout_json'])
                    self.assertEqual(self.renderer.presentation_timeline(source).times,
                                     self.renderer.presentation_timeline(video['local_path']).times)

    def test_comparison_cache_does_not_touch_application_db(self):
        cache = HeadlinePreviewCache(self.renderer)
        before = self.conn.total_changes
        result = cache.compare(headline(), self.sources[720])
        again = cache.compare(headline(), self.sources[720])
        self.assertEqual(result['artifact_id'], again['artifact_id'])
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_derived_media_artifacts').fetchone()[0], 0)

    def test_rounded_background_preserves_small_vfr_discontinuities(self):
        source=self.inputs/'rounded-vfr.mp4'
        run([self.renderer.ffmpeg,'-v','error','-y','-f','lavfi','-i','testsrc2=size=720x1280:rate=30',
             '-frames:v','12','-vf',r'settb=1/15360,setpts=N*512+if(gte(N\,4)\,191\,0)+if(gte(N\,8)\,61\,0)',
             '-vsync','0','-enc_time_base:v','1/15360','-threads','1','-c:v','libx264',source])
        result=self.renderer.render(self.conn,1,headline('BLACK_BACKGROUND'),source)
        self.assertEqual(self.renderer.presentation_timeline(source).times,
                         self.renderer.presentation_timeline(result['local_path']).times)

    def test_swatch_canonical_version_and_cache(self):
        cache = HeadlinePreviewCache(self.renderer); preset = headline()['preset']
        first = cache.swatch(preset)
        with patch('src.publisher.headline_previews.run', side_effect=AssertionError('No repeated FFmpeg process')):
            again = cache.swatch(preset)
        self.assertEqual(first, again); self.assertEqual(first['preset_version'], 2)
        self.assertTrue(first['demonstration'])
        changed = copy.deepcopy(preset); changed['version']=3; changed['spec']['text_color']='#FFFFFF'
        self.assertNotEqual(cache.swatch(changed)['image'], first['image'])

    def test_outline_real_pixels_on_light_and_dark(self):
        source=self.inputs/'contrast.mp4'
        if not source.is_file():
            run([self.renderer.ffmpeg,'-v','error','-y','-f','lavfi','-i',
                 'color=c=0x20252B:size=720x1280:rate=24,drawbox=x=0:y=0:w=iw/2:h=ih:color=0xE9EDF0:t=fill',
                 '-t','0.125','-threads','1','-c:v','libx264',source])
        for style in ('YELLOW','OUTLINE','BLACK_BACKGROUND'):
            with self.subTest(style=style):
                artifact=self.renderer.render(self.conn,1,headline(style),source,kind='preview')
                ppm=Path(artifact['local_path']).with_suffix('.ppm')
                run([self.renderer.ffmpeg,'-v','error','-y','-i',artifact['local_path'],'-frames:v','1','-pix_fmt','rgb24',ppm])
                header,pixels=ppm.read_bytes().split(b'\n255\n',1)
                self.assertIn(b'720 1280',header)
                plan=json.loads(artifact['layout_json'])
                for index,(x,y,w,h) in enumerate(plan['line_ink_boxes']):
                    for start,end in ((int(x),360),(360,int(x+w))):
                        samples=[pixels[(row*720+col)*3:(row*720+col)*3+3]
                                 for row in range(int(y)-plan['stroke'],int(y+h)+plan['stroke']+1)
                                 for col in range(start,end)]
                        black=sum(max(pixel)<20 for pixel in samples)
                        color=sum(pixel[0]>200 and pixel[1]>180 and (pixel[2]<80 if style=='YELLOW' else pixel[2]>180) for pixel in samples)
                        self.assertGreater(black,20)
                        self.assertGreater(color,20)
                    colored=[col for row in range(int(y),int(y+h)+1) for col in range(100,620)
                             if (lambda pixel: pixel[0]>200 and pixel[1]>180 and pixel[2]<80)(pixels[(row*720+col)*3:(row*720+col)*3+3])]
                    if style=='YELLOW':
                        self.assertTrue(colored)
                        self.assertLessEqual(abs((min(colored)+max(colored)+1)/2-360),3)
                if style=='BLACK_BACKGROUND':
                    x,y,w,h=plan['line_ink_boxes'][0];pad=plan['padding']
                    # Rounded corner must not be opaque black; padded edge must.
                    col,row=round(x-pad),round(y-pad)
                    self.assertGreater(max(pixels[(row*720+col)*3:(row*720+col)*3+3]),20)
                    col=round(x+w/2)
                    self.assertLess(max(pixels[(row*720+col)*3:(row*720+col)*3+3]),20)
