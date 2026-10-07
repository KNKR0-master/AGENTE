import unittest
from datetime import datetime, timezone
from types import SimpleNamespace
from unittest.mock import patch
from zoneinfo import ZoneInfo

from src.publisher.reconciliation_evidence import get_latest_reconciliation_evidence
from src.publisher.service import get_row, log
from src.publisher.worker import PublisherWorker, assess_remote_draft_rows
from tests import test_publisher_reconciliation_evidence as evidence_fixture
from tests import test_publisher_remote as remote_fixture


class ReadOnlyVerificationTests(unittest.TestCase):
    setUp = evidence_fixture.ReconciliationEvidenceTests.setUp
    fixture = evidence_fixture.ReconciliationEvidenceTests.fixture
    batch = remote_fixture.PublisherRemoteTests.batch

    def test_match_outcomes_never_infer_absence(self):
        checkpoint = datetime(2026, 9, 29, 15, 0, tzinfo=timezone.utc).isoformat()
        row = {'caption_text': 'Legenda', 'duration_text': '00:23',
               'updated_text': '29 de set de 2026, 12:00'}
        unique = assess_remote_draft_rows([row], 'Legenda', checkpoint, 23, listing_complete=True)
        self.assertEqual(unique['outcome'], 'REMOTE_EXISTS_CONFIRMED')
        self.assertEqual(assess_remote_draft_rows([row, row], 'Legenda', checkpoint, 23,
                                                  listing_complete=True)['outcome'], 'REMOTE_MATCH_AMBIGUOUS')
        self.assertEqual(assess_remote_draft_rows([], 'Legenda', checkpoint, 23,
                                                  listing_complete=True)['outcome'], 'REMOTE_STATE_UNKNOWN')
        self.assertEqual(assess_remote_draft_rows([row], 'Legenda', checkpoint, 23,
                                                  listing_complete=False)['blocker_code'], 'PARTIAL_LISTING')
        self.assertEqual(assess_remote_draft_rows([row], 'Legenda', None, 23,
                                                  listing_complete=True)['outcome'], 'REMOTE_STATE_UNKNOWN')
        self.assertEqual(assess_remote_draft_rows([row], 'Legenda', checkpoint, None,
                                                  listing_complete=True)['outcome'], 'REMOTE_STATE_UNKNOWN')

    def test_verification_appends_only_and_does_not_mutate_draft(self):
        first, second, execution = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET tiktok_username='cecilia.figueired65' WHERE id=?",
                              (self.account,))
            self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution,))
        worker = PublisherWorker(self.root, self.root / 'test.db')
        before = get_row(self.conn, 'publisher_drafts', first)
        media_before = get_row(self.conn, 'publisher_media_assets', before['media_asset_id'])
        observation = {'source_surface': 'TIKTOK_STUDIO_DRAFT_LIST',
                       'observed_account': 'cecilia.figueired65', 'rows': [],
                       'listing_complete': True, 'listing_bounds': {'visible_rows': 0}}
        with patch.object(worker, '_inspect_draft_list_read_only', return_value=observation):
            result = worker._verify_remote_draft_record(None, self.conn, first, execution)
        self.assertEqual(result['outcome'], 'REMOTE_STATE_UNKNOWN')
        self.assertIsNone(result['blocker_code'])
        import json
        saved = get_latest_reconciliation_evidence(self.conn, draft_id=first)
        observed = json.loads(saved['evidence_payload_json'])['current_listing']
        self.assertEqual(observed, {'complete': True, 'count': 0, 'match_result': 'NO_CURRENT_MATCH'})
        self.assertIsNotNone(get_latest_reconciliation_evidence(self.conn, draft_id=first))
        self.assertIsNone(get_latest_reconciliation_evidence(self.conn, draft_id=second))
        self.assertEqual(get_row(self.conn, 'publisher_drafts', first), before)
        self.assertEqual(get_row(self.conn, 'publisher_media_assets', before['media_asset_id']), media_before)

    def test_checkpoint_race_and_lifecycle_race_reject_evidence(self):
        first, _, execution = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET tiktok_username='cecilia.figueired65' WHERE id=?",
                              (self.account,))
        worker = PublisherWorker(self.root, self.root / 'test.db')

        def changed_checkpoint(_playwright, _account):
            with self.conn:
                log(self.conn, first, 'SAVE_DRAFT', 'OK', 'race fixture')
            return {'source_surface': 'TIKTOK_STUDIO_DRAFT_LIST', 'observed_account': 'cecilia.figueired65',
                    'rows': [], 'listing_complete': True, 'listing_bounds': {}}

        with patch.object(worker, '_inspect_draft_list_read_only', side_effect=changed_checkpoint):
            with self.assertRaisesRegex(ValueError, 'REMOTE_EFFECT_CHECKPOINT_MISMATCH'):
                worker._verify_remote_draft_record(None, self.conn, first, execution)
        self.assertIsNone(get_latest_reconciliation_evidence(self.conn, draft_id=first))

        def changed_lifecycle(_playwright, _account):
            with self.conn:
                self.conn.execute("UPDATE publisher_media_assets SET status='RESERVED' WHERE id=(SELECT media_asset_id FROM publisher_drafts WHERE id=?)", (first,))
            return {'source_surface': 'TIKTOK_STUDIO_DRAFT_LIST', 'observed_account': 'cecilia.figueired65',
                    'rows': [], 'listing_complete': True, 'listing_bounds': {}}

        with patch.object(worker, '_inspect_draft_list_read_only', side_effect=changed_lifecycle):
            with self.assertRaisesRegex(ValueError, 'EVIDENCE_LIFECYCLE_CHANGED'):
                worker._verify_remote_draft_record(None, self.conn, first, execution)

    def test_auth_and_editor_blockers_remain_unknown(self):
        first, _, execution = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET tiktok_username='cecilia.figueired65' WHERE id=?",
                              (self.account,))
        worker = PublisherWorker(self.root, self.root / 'test.db')
        for blocker, source in [('UNSAVED_EDITOR', 'TIKTOK_STUDIO_EDITOR'),
                                ('AUTH_REQUIRED', 'TIKTOK_STUDIO_AUTH'),
                                ('CAPTCHA', 'TIKTOK_STUDIO_AUTH'),
                                ('TWO_FACTOR', 'TIKTOK_STUDIO_AUTH'),
                                ('SECURITY_CHALLENGE', 'TIKTOK_STUDIO_AUTH'),
                                ('WRONG_ACCOUNT', 'TIKTOK_STUDIO_AUTH')]:
            with self.subTest(blocker=blocker), patch.object(worker, '_inspect_draft_list_read_only',
                    return_value={'source_surface': source, 'observed_account': None, 'rows': [],
                                  'listing_complete': False, 'blocker_code': blocker, 'listing_bounds': {}}):
                result = worker._verify_remote_draft_record(None, self.conn, first, execution)
                self.assertEqual(result['outcome'], 'REMOTE_STATE_UNKNOWN')
                self.assertEqual(result['blocker_code'], blocker)

    def test_concurrent_requests_share_one_queued_inspection(self):
        first, _, execution = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?", (execution,))
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start'), patch('src.publisher.worker.Future.result', return_value={'ok': True}):
            self.assertEqual(worker.request_draft_verification(first), {'ok': True})
            self.assertEqual(worker.request_draft_verification(first), {'ok': True})
        self.assertEqual(worker.commands.qsize(), 1)

    def test_open_editor_is_not_navigated_or_dismissed(self):
        worker = PublisherWorker(self.root, self.root / 'test.db')
        page = SimpleNamespace(url='https://www.tiktok.com/tiktokstudio/upload', is_closed=lambda: False)
        context = SimpleNamespace(pages=[page])
        worker.contexts[self.account] = context
        with patch.object(worker, 'assert_actions_allowed'), patch.object(worker, 'account_profile', return_value=self.root), \
                patch.object(worker.browser_runtime, 'session', return_value=SimpleNamespace(context=context)):
            result = worker._inspect_draft_list_read_only(None, {'id': self.account})
        self.assertEqual(result['blocker_code'], 'UNSAVED_EDITOR')
        self.assertEqual(result['source_surface'], 'TIKTOK_STUDIO_EDITOR')
        self.assertEqual(page.url, 'https://www.tiktok.com/tiktokstudio/upload')

    def test_strong_and_ambiguous_matches_record_evidence_only(self):
        first, second, execution = self.fixture()
        with self.conn:
            self.conn.execute("UPDATE publisher_accounts SET tiktok_username='cecilia.figueired65' WHERE id=?",
                              (self.account,))
        worker = PublisherWorker(self.root, self.root / 'test.db')
        worker.cache.root.mkdir(parents=True, exist_ok=True)
        now_local = datetime.now(timezone.utc).astimezone(ZoneInfo('America/Sao_Paulo'))
        months = ('jan', 'fev', 'mar', 'abr', 'mai', 'jun',
                  'jul', 'ago', 'set', 'out', 'nov', 'dez')
        updated = f'{now_local.day} de {months[now_local.month - 1]} de {now_local.year}, {now_local:%H:%M}'
        row = {'caption_text': 'Legenda', 'duration_text': '00:23', 'updated_text': updated}
        for draft_id, rows, expected in (
            (first, [row], 'REMOTE_EXISTS_CONFIRMED'),
            (second, [row, row], 'REMOTE_MATCH_AMBIGUOUS'),
        ):
            media_path = worker.cache.root / f'{draft_id}.mp4'
            media_path.write_bytes(b'fixture')
            with self.conn:
                self.conn.execute('UPDATE publisher_drafts SET temp_path=? WHERE id=?',
                                  (str(media_path), draft_id))
                log(self.conn, draft_id, 'SAVE_DRAFT', 'OK', 'fixture')
            before = get_row(self.conn, 'publisher_drafts', draft_id)
            media_before = get_row(self.conn, 'publisher_media_assets', before['media_asset_id'])
            observation = {'source_surface': 'TIKTOK_STUDIO_DRAFT_LIST',
                           'observed_account': 'cecilia.figueired65', 'rows': rows,
                           'listing_complete': True, 'listing_bounds': {'visible_rows': len(rows)}}
            with patch.object(worker, '_inspect_draft_list_read_only', return_value=observation), \
                 patch('src.publisher.worker.ensure_ffprobe_available', return_value='ffprobe'), \
                 patch('src.publisher.worker.probe_video', return_value=SimpleNamespace(results={'duration': 23.0})):
                result = worker._verify_remote_draft_record(None, self.conn, draft_id, execution)
            evidence = get_latest_reconciliation_evidence(self.conn, draft_id=draft_id)
            self.assertEqual(result['outcome'], expected)
            self.assertEqual(evidence['candidate_count'], len(rows))
            self.assertEqual(get_row(self.conn, 'publisher_drafts', draft_id), before)
            self.assertEqual(get_row(self.conn, 'publisher_media_assets', before['media_asset_id']), media_before)
