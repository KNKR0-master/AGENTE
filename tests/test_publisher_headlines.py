import copy
import json
import sqlite3
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import dashboard_server
from src.publisher import headlines as h
from src.publisher.http import PublisherAPI
from src.publisher.service import get_row, init_publisher
from tests import test_publisher_remote as remote
from tests import test_publisher_media_library as library


def spec(**changes):
    return {**h.DEFAULT_SPEC, 'font_ref': 'fixture-font', 'font_sha256': 'a' * 64, **changes}


class HeadlineContractTests(unittest.TestCase):
    setUp = remote.PublisherRemoteTests.setUp
    batch = remote.PublisherRemoteTests.batch

    def preset(self, **changes):
        return h.save_preset(self.conn, {'name': 'Visual', 'spec': spec(**changes)})['id']

    def configure(self, **changes):
        self.service.save_product({'id': self.product, 'default_headline_enabled': True,
                                   'default_headline_text': 'Oferta 20% / R$ 50!\nA\u00e7\u00e3o \U0001f31f',
                                   'default_headline_preset_id': self.preset(), **changes})

    def snapshot(self, draft_id):
        return h.read_snapshot(get_row(self.conn, 'publisher_drafts', draft_id))

    def test_disabled_defaults_and_caption_independent(self):
        draft = self.batch()[0]
        snapshot = self.snapshot(draft)
        self.assertFalse(snapshot['enabled'])
        self.assertEqual(snapshot['provenance'], 'DISABLED')
        self.assertEqual(h.readiness(snapshot), [])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft)['caption'], 'Legenda')
        self.assertFalse(self.service.get_product(self.product)['headline_required'])

    def test_full_snapshot_and_product_edit_are_independent(self):
        self.configure()
        first = self.batch()[0]
        original = self.snapshot(first)
        self.assertEqual(original['preset']['spec'], h.validate_spec(spec()))
        self.assertIn('\U0001f31f', original['text'])
        self.service.save_product({'id': self.product, 'default_headline_text': 'Novo texto'})
        second = self.service.create_remote_batch({'product_id': self.product, 'account_id': self.account,
                                                   'media_asset_ids': [2]})['draft_ids'][0]
        self.assertEqual(self.snapshot(first), original)
        self.assertEqual(self.snapshot(second)['text'], 'Novo texto')
        self.assertEqual(self.snapshot(second)['provenance'], 'PRODUCT_DEFAULT')

    def test_preset_versions_immutable_and_snapshot_self_contained(self):
        self.configure()
        first = self.batch()[0]
        original = self.snapshot(first)
        preset_id = original['preset']['id']
        h.save_preset(self.conn, {'id': preset_id, 'expected_version': 1, 'name': 'Renamed', 'spec': spec(y=0.3)})
        second = self.service.create_remote_batch({'product_id': self.product, 'account_id': self.account,
                                                   'media_asset_ids': [2]})['draft_ids'][0]
        self.assertEqual(self.snapshot(first), original)
        self.assertEqual(self.snapshot(second)['preset']['version'], 2)
        self.assertEqual(self.snapshot(second)['preset']['spec']['y'], 0.3)
        with self.assertRaises(sqlite3.IntegrityError), self.conn:
            self.conn.execute('UPDATE publisher_headline_preset_versions SET spec_json=?', ('{}',))
        with self.assertRaises(sqlite3.IntegrityError), self.conn:
            self.conn.execute('DELETE FROM publisher_headline_preset_versions')
        with self.assertRaisesRegex(ValueError, 'VERSION_CONFLICT'):
            h.save_preset(self.conn, {'id': preset_id, 'expected_version': 1, 'name': 'Stale', 'spec': spec()})
        h.save_preset(self.conn, {'id': preset_id, 'expected_version': 2, 'name': 'Archive', 'active': False, 'spec': spec()})
        self.assertEqual(self.snapshot(first), original)
        self.assertEqual(h.readiness(original), [])

    def test_override_before_freeze_and_preserves_preset_version(self):
        self.configure()
        draft = self.batch()[0]
        before = self.snapshot(draft)
        p = before['preset']['id']
        h.save_preset(self.conn, {'id': p, 'expected_version': 1, 'name': 'New', 'spec': spec(y=0.4)})
        result = self.service.override_headline(draft, {'text': 'Ajuste'})
        self.assertEqual(result['preset'], before['preset'])
        self.assertEqual(result['provenance'], 'DRAFT_OVERRIDE')
        self.assertEqual(result['text'], 'Ajuste')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft)['caption'], 'Legenda')
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_execution_logs WHERE step='HEADLINE_OVERRIDE'").fetchone()[0], 1)
        self.assertFalse(self.service.override_headline(draft, {'enabled': False})['enabled'])

    def test_override_rejected_after_remote_or_execution_boundary(self):
        self.configure()
        drafts = self.batch(3)
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (drafts[0],))
            self.conn.execute("UPDATE publisher_drafts SET status='PREPARING' WHERE id=?", (drafts[1],))
            self.conn.execute("UPDATE publisher_drafts SET persistence_status='PERSISTED' WHERE id=?", (drafts[2],))
        for draft in drafts:
            before = self.snapshot(draft)
            with self.assertRaisesRegex(ValueError, 'QUEUE_EDIT_BLOCKED'):
                self.service.override_headline(draft, {'text': 'Not allowed'})
            self.assertEqual(self.snapshot(draft), before)

    def test_required_incomplete_blocks_readiness_and_can_be_repaired(self):
        self.configure(default_headline_enabled=False, headline_required=True)
        draft = self.batch()[0]
        row = get_row(self.conn, 'publisher_drafts', draft)
        with self.assertRaisesRegex(ValueError, 'HEADLINE_REQUIRED'):
            self.service.validate_fields(row)
        result = self.service.queue_preflight(row['batch_id'])
        self.assertIn('HEADLINE_REQUIRED', result['items'][0]['blockers'])
        self.assertFalse(result['items'][0]['ready'])
        with self.assertRaisesRegex(ValueError, 'HEADLINE_REQUIRED'):
            self.service.override_headline(draft, {'enabled': False})
        repaired = self.service.override_headline(draft, {'enabled': True, 'text': 'Required', 'preset_id': self.preset()})
        self.assertEqual(h.readiness(repaired), [])
        self.service.validate_fields(get_row(self.conn, 'publisher_drafts', draft))

    def test_enabled_missing_config_and_excessive_lines_block(self):
        self.configure(default_headline_text='')
        draft = self.batch()[0]
        self.assertEqual(h.readiness(self.snapshot(draft)), ['HEADLINE_CONFIG_INCOMPLETE'])
        with self.assertRaisesRegex(ValueError, 'HEADLINE_LINES_INVALID'):
            self.service.override_headline(draft, {'text': '1\n2\n3\n4'})

    def test_hash_pixel_identity_and_canonical_order(self):
        self.configure()
        snapshot = self.snapshot(self.batch()[0])
        original = h.render_hash(snapshot)
        for key, value in [('created_at', 'tomorrow'), ('provenance', 'DRAFT_OVERRIDE'), ('required', True)]:
            other = copy.deepcopy(snapshot); other[key] = value
            self.assertEqual(h.render_hash(other), original)
        other = copy.deepcopy(snapshot)
        other['preset'].update(name='Label', id=999, version=123)
        other['preset']['spec']['font_ref'] = 'another-alias'
        other['preset']['spec'] = dict(reversed(list(other['preset']['spec'].items())))
        self.assertEqual(h.render_hash(other), original)
        other['preset']['spec']['shadow_blur_ratio'] = 0.08
        self.assertEqual(h.render_hash(other), original)
        for key, value in [('font_sha256', 'b'*64), ('y', 0.4), ('text_color', '#000000')]:
            other = copy.deepcopy(snapshot); other['preset']['spec'][key] = value
            self.assertNotEqual(h.render_hash(other), original)
        other = copy.deepcopy(snapshot); other['text'] += '!'
        self.assertNotEqual(h.render_hash(other), original)
        self.assertEqual(h.readiness(other), ['HEADLINE_CONFIG_INVALID'])

    def test_invalid_input_is_not_truncated_or_coerced(self):
        for data in [spec(x=2), spec(text_color='red'), spec(max_lines=9), spec(font_ref='C:/Windows/Fonts/a.ttf'),
                     spec(font_sha256=''), spec(font_size_ratio=float('nan')), spec(max_width=1)]:
            with self.subTest(data=data), self.assertRaises(ValueError): h.validate_spec(data)
        for value in ['x'*501, '\n'*8, 'a\x00b']:
            with self.assertRaises(ValueError): h.text_value(value)
        self.assertEqual(len(h.text_value('x'*500)), 500)
        with self.assertRaises(ValueError): h.boolean('false')

    def test_legacy_local_creation_and_duplication_keep_snapshot(self):
        self.configure()
        directory = self.root/'data/publisher/uploads'
        directory.mkdir(parents=True)
        path = directory/'fixture.mp4'
        path.write_bytes(b'fixture only, no rendering')
        draft = self.service.create_batch({'product_id': self.product, 'account_id': self.account},
                                          [('fixture.mp4', path)])['draft_ids'][0]
        original = self.snapshot(draft)
        self.service.bulk([draft], {}, 'duplicate')
        other = self.conn.execute('SELECT MAX(id) FROM publisher_drafts').fetchone()[0]
        self.assertEqual(self.snapshot(other), original)
        self.assertEqual(original['provenance'], 'PRODUCT_DEFAULT')

    def test_explicit_new_preset_override_does_not_change_other_drafts(self):
        self.configure()
        a, b = self.batch(2)
        original = self.snapshot(b)
        new = self.preset(y=0.4)
        result = self.service.override_headline(a, {'preset_id': new})
        self.assertEqual(result['preset']['id'], new)
        self.assertEqual(self.snapshot(b), original)

    def test_missing_preset_required_and_optional_enabled_are_not_ready(self):
        for required in (False, True):
            snapshot = h.make_snapshot(self.conn, {'default_headline_enabled': True,
                'default_headline_text': 'Text', 'headline_required': required})
            self.assertEqual(h.readiness(snapshot), ['HEADLINE_CONFIG_INCOMPLETE'])

    def test_queued_execution_freezes_override_even_without_upload(self):
        self.configure()
        draft = self.batch()[0]
        with self.conn:
            execution = self.conn.execute("INSERT INTO publisher_batch_executions(total,status,started_at) VALUES (1,'QUEUED','2026-10-07')").lastrowid
            self.conn.execute('INSERT INTO publisher_execution_items(execution_id,draft_id) VALUES (?,?)', (execution,draft))
        with self.assertRaisesRegex(ValueError, 'QUEUE_EDIT_BLOCKED'):
            self.service.override_headline(draft, {'text':'Frozen'})

    def test_required_policy_is_snapshot_not_current_product(self):
        self.configure(headline_required=True)
        draft = self.batch()[0]
        self.service.save_product({'id':self.product,'headline_required':False})
        with self.assertRaisesRegex(ValueError, 'HEADLINE_REQUIRED'):
            self.service.override_headline(draft, {'enabled':False})

    def test_old_schema_migration_is_additive_idempotent_and_no_backfill(self):
        draft = self.batch()[0]
        with self.conn:
            self.conn.execute('ALTER TABLE publisher_drafts DROP COLUMN headline_snapshot_json')
            for column in ('default_headline_enabled', 'default_headline_text', 'default_headline_preset_id', 'headline_required'):
                self.conn.execute(f'ALTER TABLE publisher_products DROP COLUMN {column}')
            self.conn.execute("DELETE FROM publisher_migrations WHERE name='headline_contract_v1'")
        before = {table: [dict(r) for r in self.conn.execute('SELECT * FROM '+table)]
                  for table in ('publisher_drafts', 'publisher_products', 'publisher_batches', 'publisher_media_assets')}
        init_publisher(self.conn); init_publisher(self.conn)
        for table, rows in before.items():
            after = [dict(r) for r in self.conn.execute('SELECT * FROM '+table)]
            self.assertEqual(rows, [{key: row[key] for key in old} for old, row in zip(rows, after)])
        self.assertFalse(self.snapshot(draft)['enabled'])
        self.assertEqual(self.service.get_product(self.product)['default_headline_text'], '')

    def test_official_http_contract_errors_and_zero_worker_calls(self):
        draft = self.batch()[0]
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root/'test.db', self.root, Mock()
        with patch.object(dashboard_server, 'PUBLISHER', api), patch.object(dashboard_server, 'DB_PATH', api.db):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            def request(path, body=None):
                req = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/publisher'+path,
                    data=None if body is None else json.dumps(body).encode(), headers={'Content-Type':'application/json'})
                try: response = urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as exc: response = exc
                with response: return response.status, json.load(response)
            try:
                status, p = request('/headline-presets', {'name':'API', 'spec':spec()})
                self.assertEqual(status, 200)
                self.assertEqual(request('/headline-presets')[0], 200)
                status, workflow=request('/headline-workflow')
                self.assertEqual(status,200);self.assertFalse(workflow['readiness']['ready'])
                self.assertEqual(request('/headline-templates',{'name':'Global','text':'OFERTA'})[0],200)
                self.assertEqual(request(f'/drafts/{draft}/headline')[0], 200)
                self.assertEqual(request('/drafts/999999/headline')[0], 404)
                for invalid in [spec(x=9), spec(text_color='bad'), spec(max_lines=99)]:
                    self.assertEqual(request('/headline-presets', {'name':'Invalid','spec':invalid})[0], 400)
                self.assertEqual(request(f'/drafts/{draft}/headline', {'enabled':True,'text':'Hello','preset_id':999999})[0], 400)
                self.assertEqual(request(f'/drafts/{draft}/headline', {'enabled':True,'text':'Hello','preset_id':p['id']})[0], 200)
                self.assertFalse(api.worker.mock_calls)
                self.assertFalse(self.provider.deleted or self.provider.downloads)
            finally:
                server.shutdown(); server.server_close(); thread.join(5)


class HeadlineMultiProductTests(unittest.TestCase):
    setUp = library.PublisherMediaLibraryTests.setUp
    _product = library.PublisherMediaLibraryTests._product
    _file = library.PublisherMediaLibraryTests._file

    def test_library_snapshots_each_product_and_disabled_independently(self):
        p = h.save_preset(self.conn, {'name':'Visual', 'spec':spec()})['id']
        for product, text in [(self.product_a,'Alpha'), (self.product_b,'Beta')]:
            self.service.save_product({'id':product,'default_headline_enabled':True,
                'default_headline_text':text,'default_headline_preset_id':p})
        self.service.decide_media({'drive_file_id':'low','decision':'VALID'})
        self.service.assign_media_product({'drive_file_ids':['low'],'product_id':self.product_b})
        self.service.save_product({'id':self.product_empty,'default_caption':'Caption C'})
        with patch('src.publisher.headline_configuration.machine_readiness',return_value={'ready':True}):
            ids = self.service.create_library_batch({'drive_file_ids':['high','low','empty-caption'],'account_id':self.account})['draft_ids']
        snapshots = [h.read_snapshot(get_row(self.conn,'publisher_drafts',i)) for i in ids]
        self.assertEqual([s['text'] for s in snapshots], ['Alpha','Beta',''])
        self.assertEqual([s['enabled'] for s in snapshots], [True,True,False])
