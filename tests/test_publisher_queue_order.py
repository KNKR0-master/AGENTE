import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.store import connect, init_db
from src.publisher.service import PublisherService, init_publisher, get_row, log
from src.publisher.worker import PublisherWorker
from src.publisher.queue_policy import draft_queue_capabilities
from src.publisher.account_safety import AccountSafetyGuard


class QueueOrderTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        env = patch.dict(os.environ, {'PUBLISHER_BROWSER_PROFILE_ROOT': str(self.root / 'profiles')})
        env.start()
        self.addCleanup(env.stop)
        self.db = self.root / 'test.db'
        self.conn = connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        self.service = PublisherService(self.conn, self.root)
        self.account = self.service.save_account({'name': 'Cecilia'})
        self.product_a = self.service.save_product({'name': 'A', 'default_caption': 'Caption A'})
        self.product_b = self.service.save_product({'name': 'B', 'default_caption': 'Caption B'})
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET session_verified=1,account_identity='ACCOUNT_CONFIRMED' WHERE id=?", (self.account,))
            for index in range(1, 5):
                self.conn.execute(
                    "INSERT INTO publisher_media_assets(provider,source_account,remote_file_id,filename,mime_type,size_bytes,checksum,status,created_at,updated_at) "
                    "VALUES ('google_drive','cecilia',?,?, 'video/mp4',100,?,'AVAILABLE','now','now')",
                    (f'file-{index}', f'video-{index}.mp4', f'hash-{index}'),
                )

    def batch(self):
        return self.service.create_remote_batch({
            'media_asset_ids': [1, 2, 3], 'product_id': self.product_a,
            'account_id': self.account,
        })

    def test_creation_reorder_preserves_item_identity_and_is_atomic(self):
        result = self.batch()
        batch, ids = result['batch_id'], result['draft_ids']
        before = [(get_row(self.conn, 'publisher_drafts', i)['product_id'],
                   get_row(self.conn, 'publisher_drafts', i)['caption'],
                   get_row(self.conn, 'publisher_drafts', i)['media_asset_id']) for i in ids]
        self.assertEqual([get_row(self.conn, 'publisher_drafts', i)['queue_position'] for i in ids], [1, 2, 3])
        self.service.reorder_batch(batch, ids[::-1])
        self.assertEqual([row['id'] for row in self.service.snapshot()['drafts'] if row['batch_id'] == batch], ids[::-1])
        self.assertEqual(before, [(get_row(self.conn, 'publisher_drafts', i)['product_id'],
                                   get_row(self.conn, 'publisher_drafts', i)['caption'],
                                   get_row(self.conn, 'publisher_drafts', i)['media_asset_id']) for i in ids])
        for bad in ([ids[0], ids[0], ids[2]], ids[:2], [ids[0], ids[1], 999]):
            with self.assertRaises(ValueError):
                self.service.reorder_batch(batch, bad)
            self.assertEqual([row['id'] for row in self.service.snapshot()['drafts'] if row['batch_id'] == batch], ids[::-1])
        init_publisher(self.conn)
        self.assertEqual([get_row(self.conn, 'publisher_drafts', i)['queue_position'] for i in ids], [3, 2, 1])

    def test_execution_snapshots_canonical_order_and_blocks_reorder_remove(self):
        result = self.batch()
        ids = result['draft_ids']
        self.service.reorder_batch(result['batch_id'], ids[::-1])
        worker = PublisherWorker(self.root, self.db)
        with patch.object(worker, 'start'), patch.object(worker, 'assert_actions_allowed'), patch.object(worker, 'account_profile', return_value=self.root / 'profiles'), patch.object(worker.profile_ownership, 'state', return_value='SESSION_REQUIRES_VERIFICATION'):
            execution = worker.enqueue(self.conn, ids)
        rows = self.conn.execute('SELECT draft_id FROM publisher_execution_items WHERE execution_id=? ORDER BY execution_position', (execution,)).fetchall()
        self.assertEqual([row['draft_id'] for row in rows], ids[::-1])
        with self.assertRaisesRegex(ValueError, 'BATCH_ALREADY_EXECUTED'):
            self.service.reorder_batch(result['batch_id'], ids)
        with self.assertRaisesRegex(ValueError, 'QUEUE_REMOVE_BLOCKED'):
            self.service.bulk([ids[0]], {}, 'archive')

    def test_safe_remove_releases_only_reserved_media(self):
        ids = self.batch()['draft_ids']
        self.service.bulk([ids[0]], {}, 'archive')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'AVAILABLE')
        with self.conn:
            log(self.conn, ids[1], 'UPLOAD_STARTED', 'OK', 'checkpoint')
        with self.assertRaisesRegex(ValueError, 'QUEUE_REMOVE_BLOCKED'):
            self.service.bulk([ids[1]], {}, 'archive')
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 2)['status'], 'RESERVED')

    def test_multi_product_position_and_caption_snapshot(self):
        ids = self.batch()['draft_ids']
        self.service.bulk([ids[1]], {'product_id': self.product_b, 'caption': 'Caption B'})
        self.service.save_product({'id': self.product_a, 'name': 'A', 'default_caption': 'Changed'})
        rows = [get_row(self.conn, 'publisher_drafts', draft_id) for draft_id in ids]
        self.assertEqual([row['product_id'] for row in rows], [self.product_a, self.product_b, self.product_a])
        self.assertEqual([row['caption'] for row in rows], ['Caption A', 'Caption B', 'Caption A'])
        summary = self.service.queue_preflight(rows[0]['batch_id'])
        self.assertEqual((summary['total_items'], summary['ready_items']), (3, 3))

    def test_legacy_positions_backfill_without_touching_checkpoints(self):
        result = self.batch()
        ids = result['draft_ids']
        with self.conn:
            self.conn.execute('UPDATE publisher_drafts SET queue_position=NULL WHERE batch_id=?', (result['batch_id'],))
            self.conn.execute("UPDATE publisher_drafts SET checklist_json='{" + '"file":true' + "}' WHERE id=?", (ids[0],))
            execution = self.conn.execute(
                "INSERT INTO publisher_batch_executions(batch_id,total,status,started_at) VALUES (?,3,'PAUSED','now')",
                (result['batch_id'],),
            ).lastrowid
            for draft_id in ids:
                self.conn.execute(
                    "INSERT INTO publisher_execution_items(execution_id,draft_id,status) VALUES (?,?,'WAITING_MANUAL')",
                    (execution, draft_id),
                )
        init_publisher(self.conn)
        init_publisher(self.conn)
        self.assertEqual([get_row(self.conn, 'publisher_drafts', i)['queue_position'] for i in ids], [1, 2, 3])
        rows = self.conn.execute('SELECT draft_id,execution_position,status FROM publisher_execution_items WHERE execution_id=? ORDER BY execution_position', (execution,)).fetchall()
        self.assertEqual([(row['draft_id'], row['execution_position'], row['status']) for row in rows],
                         [(ids[0], 1, 'WAITING_MANUAL'), (ids[1], 2, 'WAITING_MANUAL'), (ids[2], 3, 'WAITING_MANUAL')])
        self.assertEqual(get_row(self.conn, 'publisher_drafts', ids[0])['checklist_json'], '{"file":true}')

    def test_foreign_batch_and_remote_effect_capabilities(self):
        first = self.batch()
        other = self.service.create_remote_batch({
            'media_asset_ids': [4], 'product_id': self.product_a, 'account_id': self.account,
        })
        ids = first['draft_ids']
        with self.assertRaisesRegex(ValueError, 'QUEUE_MEMBERSHIP_MISMATCH'):
            self.service.reorder_batch(first['batch_id'], [ids[0], ids[1], other['draft_ids'][0]])
        with self.conn:
            log(self.conn, ids[0], 'UPLOAD_STARTED', 'OK', 'checkpoint')
        draft = get_row(self.conn, 'publisher_drafts', ids[0])
        media = get_row(self.conn, 'publisher_media_assets', draft['media_asset_id'])
        capabilities = draft_queue_capabilities(self.conn, draft, media)
        self.assertFalse(capabilities['can_edit'])
        self.assertFalse(capabilities['can_remove'])
        self.assertFalse(capabilities['can_reorder'])
        self.assertTrue(capabilities['can_reconcile'])
        with self.assertRaisesRegex(ValueError, 'QUEUE_REORDER_BLOCKED'):
            self.service.reorder_batch(first['batch_id'], ids)

    def test_resume_uses_execution_snapshot_not_draft_id(self):
        result = self.batch()
        ids = result['draft_ids']
        self.service.reorder_batch(result['batch_id'], ids[::-1])
        worker = PublisherWorker(self.root, self.db)
        with patch.object(worker, 'start'), patch.object(worker, 'assert_actions_allowed'), patch.object(worker, 'account_profile', return_value=self.root / 'profiles'), patch.object(worker.profile_ownership, 'state', return_value='SESSION_REQUIRES_VERIFICATION'):
            execution = worker.enqueue(self.conn, ids, auto_cleanup=True)
            while not worker.commands.empty():
                worker.commands.get_nowait()
            with self.conn:
                self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution,))
                self.conn.execute("UPDATE publisher_execution_items SET status='WAITING_MANUAL' WHERE execution_id=?", (execution,))
                self.conn.execute("UPDATE publisher_drafts SET status='DRAFT' WHERE batch_id=?", (result['batch_id'],))
                self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id IN (1,2,3)")
            worker.resume_execution(self.conn, execution)
        queued = [worker.commands.get_nowait()[1][0] for _ in ids]
        self.assertEqual(queued, ids[::-1])

    def test_account_protection_blocks_preflight_without_mutating_drafts(self):
        result = self.batch()
        AccountSafetyGuard(self.conn).activate(self.account, 'SPAM_RISK', 'TEST_SIGNAL', 'OPERATOR')
        summary = self.service.queue_preflight(result['batch_id'])
        self.assertEqual(summary['blocked_items'], 3)
        self.assertTrue(all('BLOCKED_BY_ACCOUNT_PROTECTION' in item['blockers'] for item in summary['items']))
        self.assertEqual(self.conn.execute("SELECT COUNT(*) FROM publisher_drafts WHERE status='DRAFT'").fetchone()[0], 3)


if __name__ == '__main__':
    unittest.main()
