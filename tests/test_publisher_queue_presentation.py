import json
import shutil
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from contextlib import closing
from http.server import ThreadingHTTPServer
from unittest.mock import Mock, patch

import dashboard_server
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, init_publisher
from src.store import connect
from tests import test_publisher_queue_management as fixtures


class QueuePresentationTests(unittest.TestCase):
    setUp = fixtures.QueueManagementTests.setUp
    make_batch = fixtures.QueueManagementTests.make_batch
    add_execution = fixtures.QueueManagementTests.add_execution

    def operational_state(self):
        tables = ('publisher_drafts', 'publisher_batches', 'publisher_batch_executions',
                  'publisher_execution_items', 'publisher_media_assets', 'publisher_execution_logs')
        return {table: [tuple(row) for row in self.conn.execute(f'SELECT * FROM {table} ORDER BY 1,2')]
                for table in tables}

    def change(self, action, target_type, target_id):
        return self.service.set_queue_visibility({'action': action, 'target_type': target_type, 'target_id': target_id})

    def test_additive_migration_is_idempotent_and_existing_items_visible(self):
        self.make_batch()
        before = self.operational_state()
        for _ in range(3):
            init_publisher(self.conn)
        self.assertEqual(self.operational_state(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_queue_presentation_state').fetchone()[0], 0)
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_migrations WHERE name='queue_presentation_v1'").fetchone()[0], 1)
        self.assertEqual(self.service.snapshot()['queue_presentation']['targets'], [])

    def test_all_target_types_hide_restore_without_lifecycle_or_reservation_change(self):
        batch = self.make_batch()
        execution = self.add_execution(batch['batch_id'], batch['draft_ids'])
        before = self.operational_state()
        for target_type, target_id in [('DRAFT', batch['draft_ids'][0]), ('BATCH', batch['batch_id']), ('EXECUTION', execution)]:
            with self.subTest(target_type=target_type):
                self.assertTrue(self.change('hide', target_type, target_id)['changed'])
                first = dict(self.conn.execute('SELECT * FROM publisher_queue_presentation_state').fetchone())
                self.assertFalse(self.change('hide', target_type, target_id)['changed'])
                self.assertEqual(dict(self.conn.execute('SELECT * FROM publisher_queue_presentation_state').fetchone()), first)
                self.assertEqual(self.operational_state(), before)
                self.assertTrue(self.change('restore', target_type, target_id)['changed'])
                self.assertFalse(self.change('restore', target_type, target_id)['changed'])
                self.assertEqual(self.operational_state(), before)

    def test_parent_restore_does_not_clear_explicit_draft_hide(self):
        batch = self.make_batch()
        draft_id = batch['draft_ids'][0]
        execution = self.add_execution(batch['batch_id'], batch['draft_ids'])
        for target_type, target_id in [('DRAFT', draft_id), ('BATCH', batch['batch_id']), ('EXECUTION', execution)]:
            self.change('hide', target_type, target_id)
        self.change('restore', 'BATCH', batch['batch_id'])
        self.change('restore', 'EXECUTION', execution)
        targets = self.service.snapshot()['queue_presentation']['targets']
        self.assertEqual([(row['target_type'], row['target_id']) for row in targets], [('DRAFT', draft_id)])

    def test_validation_rejects_non_publisher_and_invalid_targets(self):
        batch = self.make_batch()
        for target_type in (None, 'PRODUCT', 'publisher_drafts', [], {}):
            with self.subTest(target_type=target_type), self.assertRaises(ValueError):
                self.change('hide', target_type, batch['batch_id'])
        for target_id in (0, -1, True, '1', 1.5, None, 999999):
            with self.subTest(target_id=target_id), self.assertRaises(ValueError):
                self.change('hide', 'DRAFT', target_id)
        with self.assertRaises(ValueError):
            self.change('cancel', 'BATCH', batch['batch_id'])
        with self.assertRaises(ValueError):
            self.service.set_queue_visibility([])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_queue_presentation_state').fetchone()[0], 0)

    def test_orphan_is_reported_without_snapshot_failure_or_cleanup(self):
        self.make_batch()
        with self.conn:
            self.conn.execute("INSERT INTO publisher_queue_presentation_state(target_type,target_id,hidden_at,created_at,updated_at) VALUES ('DRAFT',999999,'now','now','now')")
        before = self.conn.total_changes
        presentation = self.service.snapshot()['queue_presentation']
        self.assertEqual(presentation['targets'], [])
        self.assertEqual(presentation['orphan_targets'][0]['target_id'], 999999)
        self.assertEqual(self.conn.total_changes, before)

    def test_hidden_attention_stays_canonical_and_historical_identity_composed(self):
        batch = self.make_batch()
        draft_id = batch['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_UNKNOWN' WHERE id=?", (draft_id,))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1")
        for _ in range(2):
            self.add_execution(batch['batch_id'], batch['draft_ids'], item_status='FAILED')
        before = self.operational_state()
        self.change('hide', 'DRAFT', draft_id)
        snapshot = self.service.snapshot()
        draft = next(row for row in snapshot['drafts'] if row['id'] == draft_id)
        self.assertEqual(draft['status'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(draft['queue_display']['kind'], 'attention')
        self.assertEqual(snapshot['history']['presentation_counts'], {'total_items': 4, 'hidden_items': 2, 'visible_items': 2})
        hidden = snapshot['queue_presentation']['history']
        self.assertEqual(len({(row['id'], row['items'][0]['draft_id']) for row in hidden}), 2)
        self.assertEqual(self.operational_state(), before)

    def test_visibility_persists_after_reopen(self):
        batch = self.make_batch()
        self.change('hide', 'BATCH', batch['batch_id'])
        with closing(connect(self.root / 'publisher-test.db')) as reopened:
            init_publisher(reopened)
            snapshot = PublisherService(reopened, self.root).snapshot()
            self.assertEqual(snapshot['queue_presentation']['targets'][0]['target_id'], batch['batch_id'])

    def test_hidden_history_outside_first_page_remains_inspectable(self):
        batch = self.make_batch()
        oldest = self.add_execution(batch['batch_id'], batch['draft_ids'])
        for _ in range(31):
            self.add_execution(batch['batch_id'], batch['draft_ids'])
        self.change('hide', 'EXECUTION', oldest)
        snapshot = self.service.snapshot()
        self.assertNotIn(oldest, [row['id'] for row in snapshot['history']['executions']])
        self.assertEqual([row['id'] for row in snapshot['queue_presentation']['history']], [oldest])

    @unittest.skipUnless(shutil.which('git'), 'git required for baseline compatibility check')
    def test_previous_release_reads_database_with_additional_table(self):
        batch = self.make_batch()
        self.change('hide', 'BATCH', batch['batch_id'])
        source = subprocess.run(['git', 'show', '9f4671b:src/publisher/service.py'], cwd=dashboard_server.ROOT,
                                capture_output=True, text=True, encoding='utf-8', check=True).stdout
        namespace = {'__name__': 'previous_publisher_service'}
        exec(compile(source, 'previous_publisher_service.py', 'exec'), namespace)
        before = self.operational_state()
        namespace['init_publisher'](self.conn)
        old_snapshot = namespace['PublisherService'](self.conn, self.root).snapshot()
        self.assertEqual(len(old_snapshot['drafts']), 2)
        self.assertEqual(self.operational_state(), before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_queue_presentation_state').fetchone()[0], 1)

    def test_official_http_route_is_local_only(self):
        batch = self.make_batch()
        api = PublisherAPI(self.root, self.root / 'publisher-test.db')
        api.worker = Mock()
        with patch.object(dashboard_server, 'PUBLISHER', api), patch.object(dashboard_server, 'DB_PATH', self.root / 'publisher-test.db'):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            threading.Thread(target=server.serve_forever, daemon=True).start()
            try:
                before = self.operational_state()
                for action in ('hide', 'hide', 'restore', 'restore'):
                    body = json.dumps({'action': action, 'target_type': 'BATCH', 'target_id': batch['batch_id']}).encode()
                    request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/publisher/queue/presentation', data=body, headers={'Content-Type': 'application/json'})
                    with urllib.request.urlopen(request, timeout=5) as response:
                        self.assertEqual(response.status, 200)
                        self.assertIn('changed', json.load(response))
                self.assertEqual(api.worker.method_calls, [])
                self.assertEqual(self.operational_state(), before)
                request = urllib.request.Request(f'http://127.0.0.1:{server.server_port}/api/publisher/queue/presentation', data=b'{"action":"hide","target_type":"PRODUCT","target_id":1}', headers={'Content-Type': 'application/json'})
                with self.assertRaises(urllib.error.HTTPError) as error:
                    urllib.request.urlopen(request, timeout=5)
                self.assertEqual(error.exception.code, 400)
            finally:
                server.shutdown()
                server.server_close()


if __name__ == '__main__':
    unittest.main()
