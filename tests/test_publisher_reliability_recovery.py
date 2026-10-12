"""Isolated regressions for local recovery; never uses operational credentials."""
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from src.publisher.headline_previews import HeadlinePreviewCache
from src.publisher.headline_tools import RenderError
from src.publisher.account_safety import AccountSafetyGuard
from src.publisher.queue_policy import local_cancel_blocker
from src.publisher.service import get_row, log
from src.publisher.thumbnails import (
    CACHE_TTL_SECONDS, FAILURE_TTL_SECONDS, STALE_TTL_SECONDS,
    Thumbnail, ThumbnailCache, ThumbnailUnavailable,
)
from tests import test_publisher_queue_management as fixtures


class LocalRecoveryTests(unittest.TestCase):
    setUp = fixtures.QueueManagementTests.setUp
    make_batch = fixtures.QueueManagementTests.make_batch
    add_execution = fixtures.QueueManagementTests.add_execution

    def test_cancel_releases_exact_reservations_and_is_idempotent(self):
        batch = self.make_batch()
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 2)
        self.assertEqual(result['reservations_released'], 2)
        logs = self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0]
        again = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertTrue(again['already_cancelled'])
        self.assertEqual(again['cancelled_drafts'], 0)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_execution_logs').fetchone()[0], logs)
        for asset_id in (1, 2):
            asset = get_row(self.conn, 'publisher_media_assets', asset_id)
            self.assertEqual(asset['status'], 'AVAILABLE')
            self.assertIsNone(asset['remote_deleted_at'])
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 3)['status'], 'AVAILABLE')

    def test_hidden_batch_remains_reserved_until_explicit_cancel(self):
        batch = self.make_batch()
        self.service.set_queue_visibility({'action': 'hide', 'target_type': 'BATCH', 'target_id': batch['batch_id']})
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')
        self.assertEqual(self.service.cancel_batch(batch['batch_id'], eligible_only=True)['cancelled_drafts'], 2)

    def test_mixed_batch_keeps_remote_item_and_snapshot_unchanged(self):
        batch = self.make_batch()
        blocked = batch['draft_ids'][0]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET upload_status='TRANSFER_UNKNOWN' WHERE id=?", (blocked,))
            self.conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=1")
        before = get_row(self.conn, 'publisher_drafts', blocked)
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual((result['cancelled_drafts'], result['blocked_drafts']), (1, 1))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', blocked), before)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'NEEDS_RECONCILIATION')

    def test_active_execution_blocks_every_item(self):
        batch = self.make_batch()
        self.add_execution(batch['batch_id'], batch['draft_ids'][:1], status='RUNNING', item_status='PROCESSING')
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual((result['cancelled_drafts'], result['blocked_drafts']), (0, 2))

    def test_checkpoint_blocks_even_if_flags_look_pristine(self):
        batch = self.make_batch()
        for step in ('UPLOAD_STARTED', 'SAVE_DRAFT', 'TEST_FINAL_RESULT', 'UPLOAD_CONFIRMED'):
            with self.subTest(step=step):
                with self.conn:
                    log(self.conn, batch['draft_ids'][0], step, 'OK', 'fixture')
                self.assertEqual(local_cancel_blocker(self.conn, get_row(self.conn, 'publisher_drafts', batch['draft_ids'][0])),
                                 'REMOTE_EFFECT_POSSIBLE')

    def test_persisted_item_is_not_cancelled_or_consumed(self):
        batch = self.make_batch(asset_ids=(1,))
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET persistence_status='PERSISTED' WHERE id=?", (batch['draft_ids'][0],))
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 0)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', 1)['status'], 'RESERVED')

    def test_mutation_ledger_blocks_even_without_upload_log(self):
        batch = self.make_batch(asset_ids=(1,))
        AccountSafetyGuard(self.conn).start_mutation(self.account, 'fixture-upload', 'UPLOAD', draft_id=batch['draft_ids'][0])
        before = get_row(self.conn, 'publisher_drafts', batch['draft_ids'][0])
        result = self.service.cancel_batch(batch['batch_id'], eligible_only=True)
        self.assertEqual(result['cancelled_drafts'], 0)
        self.assertEqual(result['items'][0]['reason'], 'REMOTE_EFFECT_POSSIBLE')
        self.assertEqual(get_row(self.conn, 'publisher_drafts', batch['draft_ids'][0]), before)

    def test_proven_local_failure_can_cancel_without_thawing_snapshot(self):
        batch = self.make_batch(asset_ids=(1,))
        draft_id = batch['draft_ids'][0]
        execution_id = self.add_execution(batch['batch_id'], [draft_id], status='PAUSED', item_status='WAITING_MANUAL')
        with self.conn:
            self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=1")
            log(self.conn, draft_id, 'HEADLINE_PREPARATION_FAILED', 'BLOCKED', 'HEADLINE_OUTPUT_CADENCE_INVALID')
        before = get_row(self.conn, 'publisher_drafts', draft_id)['headline_snapshot_json']
        with self.assertRaisesRegex(ValueError, 'QUEUE_EDIT_BLOCKED'):
            self.service.override_headline(draft_id, {'text': 'Do not thaw'})
        self.assertEqual(self.service.cancel_batch(batch['batch_id'], eligible_only=True)['cancelled_drafts'], 1)
        self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id)['headline_snapshot_json'], before)
        item = self.conn.execute('SELECT status,error_code FROM publisher_execution_items WHERE execution_id=?',
                                 (execution_id,)).fetchone()
        self.assertEqual(tuple(item), ('FAILED', 'LOCAL_BATCH_CANCELLED'))
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_batch_executions').fetchone()[0], 1)
        execution = self.conn.execute('SELECT status FROM publisher_batch_executions WHERE id=?',
                                      (execution_id,)).fetchone()
        self.assertIsNotNone(execution)
        self.assertEqual(execution['status'], 'COMPLETED')

    def test_unknown_historical_execution_is_not_proof_of_safety(self):
        batch = self.make_batch(asset_ids=(1,))
        self.add_execution(batch['batch_id'], batch['draft_ids'], status='PAUSED', item_status='WAITING_MANUAL')
        self.assertEqual(self.service.cancel_batch(batch['batch_id'], eligible_only=True)['cancelled_drafts'], 0)


class ThumbnailRecoveryTests(unittest.TestCase):
    def test_transient_refresh_failure_preserves_only_same_identity(self):
        clock = [0]
        cache = ThumbnailCache(clock=lambda: clock[0])
        image = Thumbnail(b'\xff\xd8good', 'image/jpeg')
        cache.get('one', lambda _: image)
        clock[0] = CACHE_TTL_SECONDS + 1
        def fail(_):
            raise ThumbnailUnavailable('THUMBNAIL_FETCH_FAILED')
        self.assertEqual(cache.get('one', fail), image)
        with self.assertRaises(ThumbnailUnavailable):
            cache.get('two', fail)
        clock[0] += STALE_TTL_SECONDS + 1
        with self.assertRaises(ThumbnailUnavailable):
            cache.get('one', fail)

    def test_negative_cache_and_retry_are_bounded(self):
        clock = [0]
        cache = ThumbnailCache(clock=lambda: clock[0])
        calls = []
        def missing(key):
            calls.append(key)
        for _ in range(10):
            self.assertIsNone(cache.get('one', missing))
        self.assertEqual(calls, ['one'])
        clock[0] += FAILURE_TTL_SECONDS + 1
        cache.get('one', missing)
        self.assertEqual(calls, ['one', 'one'])

    def test_identity_mismatch_never_returns_old_image(self):
        clock = [0]
        cache = ThumbnailCache(clock=lambda: clock[0])
        cache.get('one', lambda _: Thumbnail(b'good', 'image/png'))
        clock[0] += CACHE_TTL_SECONDS + 1
        def mismatch(_):
            raise ThumbnailUnavailable('REMOTE_MEDIA_IDENTITY_MISMATCH')
        for _ in range(2):
            with self.assertRaisesRegex(ThumbnailUnavailable, 'IDENTITY_MISMATCH'):
                cache.get('one', mismatch)


class StyleSwatchRecoveryTests(unittest.TestCase):
    def test_swatch_needs_no_source_video_and_preserves_version(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer = SimpleNamespace(artifact_root=Path(directory), version='fixture', fonts={'classic': Path('fixture.ttf')},
                                       ffmpeg='fixture-ffmpeg')
            cache = HeadlinePreviewCache(renderer)
            preset = {'id': 2, 'version': 1, 'name': 'White', 'spec': {'fixture': True}}
            plan = {'box': [0, 100, 100, 100]}
            calls = []
            def fake_run(args, **kwargs):
                calls.append(args)
                Path(args[-1]).write_bytes(b'\x89PNG\r\n\x1a\nfixture')
            with patch('src.publisher.headline_previews.layout', return_value=plan) as layout_mock, \
                    patch('src.publisher.headline_previews.filters', return_value='[0:v]null[out]'), \
                    patch('src.publisher.headline_previews.run', side_effect=fake_run):
                first = cache.swatch(preset)
                self.assertEqual(cache.swatch(preset), first)
            self.assertEqual(first['preset_version'], 1)
            self.assertTrue(first['demonstration'])
            self.assertEqual(len(calls), 1)
            self.assertFalse(list(Path(directory).rglob('*.mp4')))
            self.assertEqual(layout_mock.call_args.args[1], preset['spec'])

    def test_font_failure_is_not_disguised_as_layout_failure(self):
        with tempfile.TemporaryDirectory() as directory:
            renderer = SimpleNamespace(artifact_root=Path(directory), version='fixture', fonts={}, ffmpeg='fixture')
            cache = HeadlinePreviewCache(renderer)
            with patch('src.publisher.headline_previews.layout', side_effect=RenderError('HEADLINE_FONT_UNAVAILABLE')), \
                    patch('src.publisher.headline_previews.run') as run_mock:
                with self.assertRaisesRegex(RenderError, 'HEADLINE_FONT_UNAVAILABLE'):
                    cache.swatch({'id': 2, 'version': 1, 'name': 'White', 'spec': {}})
            run_mock.assert_not_called()
