import io
import json
import unittest
from unittest.mock import Mock, patch

from src.publisher.draft_listing import sanitized_listing_probe
from src.publisher.worker import PublisherWorker
from tests import test_publisher_reconciliation_evidence as fixture


class ListingProbeTests(unittest.TestCase):
    setUp = fixture.ReconciliationEvidenceTests.setUp

    def test_probe_calls_verifier_reader_without_draft_or_evidence_writes(self):
        worker = PublisherWorker(self.root, self.root / 'test.db')
        observation = {'rows': [{'caption_text': 'private caption'}], 'listing_complete': True,
                       'hydration_samples': [{'complete': True}], 'auth_result': 'AUTHENTICATED'}
        before = self.conn.total_changes
        with patch.object(worker, '_inspect_draft_list_read_only', return_value=observation) as reader:
            result = worker.inspect_current_tiktok_draft_listing(None, self.conn, self.account)
        self.assertEqual(self.conn.total_changes, before)
        self.assertEqual(self.conn.execute('SELECT COUNT(*) FROM publisher_reconciliation_evidence').fetchone()[0], 0)
        self.assertEqual(result['current_draft_count'], 1)
        self.assertNotIn('private caption', json.dumps(result))
        self.assertNotIn('rows', result)
        self.assertFalse(result['draft_evidence_written'])
        self.assertEqual(reader.call_args.args[1]['id'], self.account)

    def test_probe_does_not_start_an_absent_runtime(self):
        worker = PublisherWorker(self.root, self.root / 'test.db')
        with patch.object(worker, 'start') as start:
            with self.assertRaisesRegex(ValueError, 'OFFICIAL_RUNTIME_REQUIRED'):
                worker.request_current_draft_listing(self.account)
        start.assert_not_called()
        self.assertTrue(worker.commands.empty())

    def test_http_requires_explicit_read_and_no_draft_id(self):
        from src.publisher.http import PublisherAPI
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root / 'test.db', self.root, Mock()
        api.worker.request_current_draft_listing.return_value = {'listing_complete': True, 'current_draft_count': 0}
        for confirmed in (False, True):
            body = json.dumps({'account_id': self.account, 'human_confirmed': confirmed}).encode()
            handler = Mock()
            handler.headers = {'Content-Type': 'application/json', 'Content-Length': str(len(body))}
            handler.rfile = io.BytesIO(body)
            if confirmed:
                api.post(handler, '/api/publisher/inspect-current-draft-listing')
                api.worker.request_current_draft_listing.assert_called_once_with(self.account)
            else:
                with self.assertRaisesRegex(ValueError, 'EXPLICIT_READ_ONLY'):
                    api.post(handler, '/api/publisher/inspect-current-draft-listing')
                api.worker.request_current_draft_listing.assert_not_called()
        api.worker.request_draft_verification.assert_not_called()

    def test_sanitized_projection_drops_arbitrary_raw_fields(self):
        result = sanitized_listing_probe({'rows': [], 'listing_complete': True, 'raw_dom': 'secret',
                                         'url': 'https://example.test/?secret=hidden'})
        self.assertNotIn('secret', json.dumps(result))
        self.assertEqual(result['current_draft_count'], 0)
