import json
import os
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import patch
from urllib.parse import urlencode

import dashboard_server
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, get_row, init_publisher
from src.store import connect, init_db


class QueueManagementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        profile_patch = patch.dict(os.environ, {'PUBLISHER_BROWSER_PROFILE_ROOT': str(self.root / 'profiles')})
        profile_patch.start()
        self.addCleanup(profile_patch.stop)
        self.conn = connect(self.root / 'publisher-test.db')
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.root)
        self.account = self.service.save_account({'name': 'Cecilia'})
        self.other_account = self.service.save_account({'name': 'Conta alternativa'})
        self.product = self.service.save_product({'name': 'Produto', 'default_caption': 'Legenda individual'})
        self.preset = self.service.save_preset({'name': 'Preset alternativo', 'account_id': self.other_account})
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_accounts SET session_verified=1,account_identity='ACCOUNT_CONFIRMED' WHERE id=?",
                (self.account,),
            )
            for index in range(1, 5):
                self.conn.execute(
                    "INSERT INTO publisher_media_assets(provider,source_account,remote_file_id,filename,mime_type,size_bytes,checksum,status,created_at,updated_at) "
                    "VALUES ('google_drive','cecilia',?,?, 'video/mp4',100,?,'AVAILABLE','now','now')",
                    (f'queue-file-{index}', f'queue-{index}.mp4', f'queue-hash-{index}'),
                )

    def make_batch(self, asset_ids=(1, 2)):
        return self.service.create_remote_batch({
            'name': 'Lote inicial', 'media_asset_ids': list(asset_ids),
            'product_id': self.product, 'account_id': self.account,
        })

    def add_execution(self, batch_id, draft_ids, *, status='COMPLETED', item_status='SUCCEEDED'):
        with self.conn:
            execution_id = self.conn.execute(
                'INSERT INTO publisher_batch_executions(batch_id,total,status,started_at,finished_at) VALUES (?,?,?,?,?)',
                (batch_id, len(draft_ids), status, '2026-09-01T10:00:00Z', '2026-09-01T10:01:00Z'),
            ).lastrowid
            for position, draft_id in enumerate(draft_ids, 1):
                self.conn.execute(
                    'INSERT INTO publisher_execution_items(execution_id,draft_id,execution_position,status) VALUES (?,?,?,?)',
                    (execution_id, draft_id, position, item_status),
                )
        return execution_id

    def post_cancel_http(self, batch_id, payload):
        db_path = self.root / 'publisher-test.db'
        api = PublisherAPI(self.root, db_path)
        with patch.object(dashboard_server, 'PUBLISHER', api), patch.object(dashboard_server, 'DB_PATH', db_path):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f'http://127.0.0.1:{server.server_port}/api/publisher/batches/{batch_id}/cancel',
                    data=json.dumps(payload).encode('utf-8'),
                    headers={'Content-Type': 'application/json'},
                    method='POST',
                )
                try:
                    with urllib.request.urlopen(request, timeout=5) as response:
                        return response.status, json.load(response)
                except urllib.error.HTTPError as response:
                    return response.code, json.load(response)
            finally:
                server.shutdown()
                server.server_close()

    def get_history_http(self, query):
        db_path = self.root / 'publisher-test.db'
        api = PublisherAPI(self.root, db_path)
        with patch.object(dashboard_server, 'PUBLISHER', api), patch.object(dashboard_server, 'DB_PATH', db_path):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                request = urllib.request.Request(
                    f'http://127.0.0.1:{server.server_port}/api/publisher/execution-history?{urlencode(query)}',
                    method='GET',
                )
                with urllib.request.urlopen(request, timeout=5) as response:
                    return response.status, json.load(response)
            finally:
                server.shutdown()
                server.server_close()

    def test_pending_batch_rename_and_configuration_preserve_draft_snapshots(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        before = [get_row(self.conn, 'publisher_drafts', draft_id) for draft_id in draft_ids]
        caps = next(row['queue_capabilities'] for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertTrue(caps['can_rename'])
        self.assertTrue(caps['can_edit'])
        self.assertTrue(caps['can_edit_configuration'])
        self.assertTrue(caps['can_reorder'])
        self.assertTrue(caps['can_cancel'])
        snapshot_drafts = {draft['id']: draft for draft in self.service.snapshot()['drafts']}
        for draft_id in draft_ids:
            self.assertTrue(snapshot_drafts[draft_id]['queue_capabilities']['can_remove'])
        self.assertNotIn('can_remove', caps)
        self.assertNotIn('can_remove_item', caps)
        self.assertTrue(caps['can_cancel'])
        self.assertEqual(caps['editable_fields'], ['account_id', 'preset_id'])

        self.service.rename_batch(batch_id, 'Lote revisado')
        self.service.edit_batch_configuration(batch_id, {
            'account_id': self.other_account, 'preset_id': self.preset,
        })
        self.assertEqual(get_row(self.conn, 'publisher_batches', batch_id)['name'], 'Lote revisado')
        after = [get_row(self.conn, 'publisher_drafts', draft_id) for draft_id in draft_ids]
        self.assertEqual([row['id'] for row in after], draft_ids)
        for old, new in zip(before, after):
            self.assertEqual(new['account_id'], self.other_account)
            self.assertEqual(new['preset_id'], self.preset)
            self.assertEqual(new['product_id'], old['product_id'])
            self.assertEqual(new['caption'], old['caption'])
            self.assertEqual(new['media_asset_id'], old['media_asset_id'])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM publisher_execution_logs WHERE step='BATCH_CONFIGURATION_EDIT'",
        ).fetchone()[0], len(draft_ids))

    def test_rename_edit_and_cancel_are_rejected_after_execution(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        self.add_execution(batch_id, draft_ids[:1], status='PAUSED', item_status='WAITING_MANUAL')
        caps = next(row['queue_capabilities'] for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertFalse(caps['can_rename'])
        self.assertFalse(caps['can_edit_configuration'])
        self.assertFalse(caps['can_reorder'])
        self.assertFalse(caps['can_cancel'])
        for operation in (
            lambda: self.service.rename_batch(batch_id, 'Nome inseguro'),
            lambda: self.service.edit_batch_configuration(batch_id, {'account_id': self.other_account}),
            lambda: self.service.cancel_batch(batch_id),
        ):
            with self.assertRaises(ValueError):
                operation()
        self.assertEqual([get_row(self.conn, 'publisher_media_assets', asset_id)['status'] for asset_id in (1, 2)],
                         ['RESERVED', 'RESERVED'])

    def test_safe_cancel_archives_drafts_releases_eligible_assets_and_keeps_audit(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='FAILED' WHERE id=2")
        caps = next(row['queue_capabilities'] for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertTrue(caps['can_cancel'])
        cancelled = self.service.cancel_batch(batch_id)
        self.assertEqual(cancelled['cancelled_drafts'], 2)
        self.assertEqual(cancelled['reservations_released'], 2)
        self.assertTrue(cancelled['audit_preserved'])
        self.assertEqual(get_row(self.conn, 'publisher_batches', batch_id)['name'], 'Lote inicial')
        self.assertEqual([get_row(self.conn, 'publisher_drafts', draft_id)['status'] for draft_id in draft_ids],
                         ['ARCHIVED', 'ARCHIVED'])
        self.assertEqual([get_row(self.conn, 'publisher_media_assets', asset_id)['status'] for asset_id in (1, 2)],
                         ['AVAILABLE', 'AVAILABLE'])
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM publisher_execution_logs WHERE step='BATCH_CANCELLED'",
        ).fetchone()[0], 2)

    def test_cancel_http_without_human_confirmation_rejects_without_mutation(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        before = {
            'batch': dict(get_row(self.conn, 'publisher_batches', batch_id)),
            'drafts': [dict(get_row(self.conn, 'publisher_drafts', draft_id)) for draft_id in draft_ids],
            'media': [dict(get_row(self.conn, 'publisher_media_assets', asset_id)) for asset_id in (1, 2)],
            'logs': self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0],
        }

        status, body = self.post_cancel_http(batch_id, {})

        self.assertEqual(status, 400)
        self.assertEqual(body['error'], 'EXPLICIT_BATCH_CANCEL_CONFIRMATION_REQUIRED')
        self.assertEqual(dict(get_row(self.conn, 'publisher_batches', batch_id)), before['batch'])
        self.assertEqual([dict(get_row(self.conn, 'publisher_drafts', draft_id)) for draft_id in draft_ids], before['drafts'])
        self.assertEqual([dict(get_row(self.conn, 'publisher_media_assets', asset_id)) for asset_id in (1, 2)], before['media'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0], before['logs'])

    def test_cancel_http_with_human_confirmation_releases_and_preserves_audit(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        execution_count = self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0]

        status, body = self.post_cancel_http(batch_id, {'human_confirmed': True})

        self.assertEqual(status, 200)
        self.assertTrue(body['audit_preserved'])
        self.assertEqual(body['cancelled_drafts'], 2)
        self.assertEqual(get_row(self.conn, 'publisher_batches', batch_id)['id'], batch_id)
        self.assertEqual([get_row(self.conn, 'publisher_drafts', draft_id)['status'] for draft_id in draft_ids], ['ARCHIVED', 'ARCHIVED'])
        self.assertEqual([get_row(self.conn, 'publisher_media_assets', asset_id)['status'] for asset_id in (1, 2)], ['AVAILABLE', 'AVAILABLE'])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0], execution_count)
        self.assertEqual(self.conn.execute(
            "SELECT COUNT(*) FROM publisher_execution_logs WHERE step='BATCH_CANCELLED'",
        ).fetchone()[0], 2)

    def test_central_reorder_capability_matches_safe_and_unsafe_mutation(self):
        result = self.make_batch()
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        batch = next(row for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertTrue(batch['queue_capabilities']['can_reorder'])
        reordered = self.service.reorder_batch(batch_id, list(reversed(draft_ids)))
        self.assertEqual(reordered['ordered_draft_ids'], list(reversed(draft_ids)))
        prior_positions = [get_row(self.conn, 'publisher_drafts', draft_id)['queue_position'] for draft_id in draft_ids]

        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (draft_ids[0],))
        batch = next(row for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertFalse(batch['queue_capabilities']['can_reorder'])
        with self.assertRaisesRegex(ValueError, 'QUEUE_REORDER_BLOCKED'):
            self.service.reorder_batch(batch_id, draft_ids)
        self.assertEqual(
            [get_row(self.conn, 'publisher_drafts', draft_id)['queue_position'] for draft_id in draft_ids],
            prior_positions,
        )

    def test_cancel_rejects_possible_remote_effect_without_releasing_media(self):
        result = self.make_batch()
        batch_id, draft_id = result['batch_id'], result['draft_ids'][0]
        self.add_execution(batch_id, [draft_id], status='PAUSED', item_status='WAITING_MANUAL')
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_UNKNOWN' WHERE id=?", (draft_id,))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1")
        before_logs = self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0]
        with self.assertRaisesRegex(ValueError, 'BATCH_CANCEL_BLOCKED'):
            self.service.cancel_batch(batch_id)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['status'], 'MANUAL_ACTION_REQUIRED')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'NEEDS_RECONCILIATION')
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0], before_logs)

    def test_cancel_rejects_processing_upload_unknown_reconciliation_and_persisted_items(self):
        result = self.make_batch(asset_ids=(1, 2, 3, 4))
        batch_id, draft_ids = result['batch_id'], result['draft_ids']
        with self.conn:
            self.conn.execute(
                "UPDATE publisher_drafts SET status='PREPARING',upload_status='UPLOAD_STARTED' WHERE id=?",
                (draft_ids[0],),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='PROCESSING' WHERE id=1")
            self.conn.execute(
                "UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_UNKNOWN' WHERE id=?",
                (draft_ids[1],),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=2")
            self.conn.execute(
                "UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED',confirmed_at='now' WHERE id=?",
                (draft_ids[2],),
            )
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED_PENDING_DELETE' WHERE id=3")
            self.conn.execute("UPDATE publisher_media_assets SET status='PROCESSING' WHERE id=4")
        batch = next(row for row in self.service.snapshot()['batches'] if row['id'] == batch_id)
        self.assertFalse(batch['queue_capabilities']['can_cancel'])
        before_drafts = [dict(get_row(self.conn, 'publisher_drafts', draft_id)) for draft_id in draft_ids]
        before_media = [dict(get_row(self.conn, 'publisher_media_assets', asset_id)) for asset_id in (1, 2, 3, 4)]
        before_logs = self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0]

        with self.assertRaisesRegex(ValueError, 'BATCH_CANCEL_BLOCKED'):
            self.service.cancel_batch(batch_id)

        self.assertEqual([dict(get_row(self.conn, 'publisher_drafts', draft_id)) for draft_id in draft_ids], before_drafts)
        self.assertEqual([dict(get_row(self.conn, 'publisher_media_assets', asset_id)) for asset_id in (1, 2, 3, 4)], before_media)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0], before_logs)

    def test_history_is_bounded_and_uses_execution_draft_pair_identity(self):
        result = self.make_batch(asset_ids=(1,))
        batch_id, draft_id = result['batch_id'], result['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED' WHERE id=?", (draft_id,))
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED' WHERE id=1")
        for _ in range(35):
            self.add_execution(batch_id, [draft_id])

        snapshot = self.service.snapshot()
        first = snapshot['history']
        self.assertEqual(first['total_count'], 35)
        self.assertEqual(len(first['executions']), 30)
        self.assertFalse(snapshot['executions'])
        identity_pairs = {(execution['id'], item['draft_id']) for execution in first['executions'] for item in execution['items']}
        self.assertEqual(len(identity_pairs), 30)
        self.assertEqual({draft for _, draft in identity_pairs}, {draft_id})
        second = self.service.execution_history(before_id=first['next_before_id'], limit=30)
        self.assertEqual(len(second['executions']), 5)
        self.assertFalse(second['has_more'])
        self.assertTrue(identity_pairs.isdisjoint(
            {(execution['id'], item['draft_id']) for execution in second['executions'] for item in execution['items']}
        ))

    def test_http_execution_history_dispatches_both_read_only_pages(self):
        result = self.make_batch(asset_ids=(1,))
        batch_id, draft_id = result['batch_id'], result['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED' WHERE id=?", (draft_id,))
            self.conn.execute("UPDATE publisher_media_assets SET status='CONSUMED' WHERE id=1")
        for _ in range(31):
            self.add_execution(batch_id, [draft_id])
        before_counts = (
            self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0],
            self.conn.execute('SELECT COUNT(*) FROM publisher_drafts').fetchone()[0],
            self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0],
        )

        status, first = self.get_history_http({'limit': '30'})
        self.assertEqual(status, 200)
        self.assertEqual(len(first['executions']), 30)
        self.assertTrue(first['has_more'])
        status, second = self.get_history_http({
            'limit': '30', 'before_id': str(first['next_before_id']),
        })
        self.assertEqual(status, 200)
        self.assertEqual(len(second['executions']), 1)
        self.assertFalse(second['has_more'])
        self.assertNotEqual(first['executions'][-1]['id'], second['executions'][0]['id'])
        self.assertEqual(before_counts, (
            self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0],
            self.conn.execute('SELECT COUNT(*) FROM publisher_drafts').fetchone()[0],
            self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0],
        ))

    def test_no_queue_history_schema_or_persistent_archive_columns_added(self):
        batch_columns = {row['name'] for row in self.conn.execute('PRAGMA table_info(publisher_batches)')}
        self.assertEqual(batch_columns, {'id', 'name', 'source', 'created_at'})
        self.assertNotIn('queue_history', batch_columns)
        self.assertNotIn('archived_from_queue', batch_columns)


if __name__ == '__main__':
    unittest.main()
