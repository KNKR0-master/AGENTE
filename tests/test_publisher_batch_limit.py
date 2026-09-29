import unittest
from unittest.mock import patch

from src.publisher.batch_limits import BatchLimitExceeded, MAX_DRAFTS_PER_BATCH
from src.publisher.worker import PublisherWorker
from tests import test_publisher_remote as remote_fixture


class BatchLimitTests(unittest.TestCase):
    setUp = remote_fixture.PublisherRemoteTests.setUp

    def _extend_inventory(self, end=31):
        for index in range(20, end):
            self.provider.files.append({
                'id': f'remote-{index}', 'name': f'FINAL_{index}.mp4',
                'mimeType': 'video/mp4', 'size': 10 + index,
                'md5Checksum': f'hash-{index}', 'parents': ['ceci'],
            })
        self.service.index_media(self.provider)

    def _remote_payload(self, count):
        return {'product_id': self.product, 'account_id': self.account,
                'media_asset_ids': list(range(1, count + 1))}

    def test_remote_service_limit_and_atomic_rejection(self):
        self.assertEqual(MAX_DRAFTS_PER_BATCH, 30)
        self._extend_inventory()
        before = self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0]
        for count in (31, 100):
            with self.subTest(count=count), self.assertRaises(BatchLimitExceeded) as captured:
                self.service.create_remote_batch(self._remote_payload(count))
            self.assertEqual((captured.exception.code, captured.exception.max, captured.exception.actual),
                             ('BATCH_LIMIT_EXCEEDED', 30, count))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0], before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_drafts').fetchone()[0], 0)
        created = self.service.create_remote_batch(self._remote_payload(30))
        self.assertEqual(len(created['draft_ids']), 30)

    def test_legacy_local_create_limit(self):
        uploads = self.root / 'data' / 'publisher' / 'uploads'
        uploads.mkdir(parents=True)
        files = []
        for index in range(31):
            path = uploads / f'item-{index}.mp4'
            path.write_bytes(b'fixture')
            files.append((path.name, str(path)))
        data = {'product_id': self.product, 'account_id': self.account, 'caption': 'Legenda'}
        with self.assertRaises(BatchLimitExceeded):
            self.service.create_batch(data, files)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batches').fetchone()[0], 0)
        created = self.service.create_batch(data, files[:30])
        self.assertEqual(len(created['draft_ids']), 30)

    def test_oversized_historical_batch_is_preserved_but_new_execution_blocked(self):
        self._extend_inventory()
        created = self.service.create_remote_batch(self._remote_payload(30))
        with self.conn:
            self.conn.execute('''INSERT INTO publisher_drafts
              (batch_id,product_id,account_id,media_asset_id,video_path,filename,caption,
               created_at,updated_at,queue_position)
              SELECT batch_id,product_id,account_id,31,'','EXTRA.mp4',caption,
                     created_at,updated_at,31 FROM publisher_drafts WHERE id=?''',
                              (created['draft_ids'][0],))
        extra = self.conn.execute('SELECT id FROM publisher_drafts WHERE batch_id=? ORDER BY id DESC LIMIT 1',
                                  (created['batch_id'],)).fetchone()[0]
        preflight = self.service.queue_preflight(created['batch_id'])
        self.assertEqual(preflight['total_items'], 31)
        self.assertTrue(all('BATCH_LIMIT_EXCEEDED' in item['blockers'] for item in preflight['items']))
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'), self.assertRaises(BatchLimitExceeded):
            worker.enqueue(self.conn, [created['draft_ids'][0]])
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_drafts').fetchone()[0], 31)
        self.assertEqual(self.conn.execute('SELECT status FROM publisher_drafts WHERE id=?', (extra,)).fetchone()[0], 'DRAFT')

    def test_execution_can_span_two_valid_batches_without_changing_batch_unit(self):
        self._extend_inventory(end=40)
        first = self.service.create_remote_batch(self._remote_payload(20))['draft_ids']
        second = self.service.create_remote_batch({
            'product_id': self.product, 'account_id': self.account,
            'media_asset_ids': list(range(21, 41)),
        })['draft_ids']
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'):
            execution = worker.enqueue(self.conn, first + second)
        self.assertEqual(self.conn.execute('SELECT total FROM publisher_batch_executions WHERE id=?',
                                           (execution,)).fetchone()[0], 40)
