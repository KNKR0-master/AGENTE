"""Batch review contracts through the official server, with isolated data."""

import json
import subprocess
import threading
import unittest
import urllib.error
import urllib.request
from http.server import ThreadingHTTPServer
from pathlib import Path
from unittest.mock import Mock, patch

import dashboard_server
from src.publisher.http import PublisherAPI
from src.publisher.service import get_row
from tests import test_publisher_remote as fixtures


class BatchReviewRoutingTests(unittest.TestCase):
    setUp = fixtures.PublisherRemoteTests.setUp
    batch = fixtures.PublisherRemoteTests.batch
    confirm = fixtures.PublisherRemoteTests.confirm

    def test_official_review_registration_and_visibility_are_local_only(self):
        ids = self.batch(6)
        for draft_id in ids[:5]:
            self.confirm(draft_id)
        abandoned = ids[-1]
        with self.conn:
            self.conn.execute("UPDATE publisher_drafts SET status='ARCHIVED' WHERE id=?", (abandoned,))
            self.conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE' WHERE id=6")
        batch_id = get_row(self.conn, 'publisher_drafts', ids[0])['batch_id']
        api = PublisherAPI.__new__(PublisherAPI)
        api.db, api.root, api.worker = self.root / 'test.db', self.root, Mock()
        with patch.object(dashboard_server, 'PUBLISHER', api), \
             patch.object(dashboard_server, 'DB_PATH', api.db):
            server = ThreadingHTTPServer(('127.0.0.1', 0), dashboard_server.Handler)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()

            def request(path, data=None):
                req = urllib.request.Request(
                    f'http://127.0.0.1:{server.server_port}/api/publisher' + path,
                    data=None if data is None else json.dumps(data).encode(),
                    headers={'Content-Type': 'application/json'})
                try:
                    response = urllib.request.urlopen(req, timeout=5)
                except urllib.error.HTTPError as exc:
                    response = exc
                with response:
                    self.assertIn('application/json', response.headers['Content-Type'])
                    return response.status, json.load(response)

            try:
                status, review = request(f'/batches/{batch_id}/review')
                self.assertEqual(status, 200)
                self.assertEqual([i['draft_id'] for i in review['items']], ids)
                self.assertEqual(review['eligible_ids'], ids[:5])
                self.assertEqual((review['prepared'], review['abandoned'], review['published'], review['attention']), (5, 1, 0, 0))
                self.assertEqual(review['items'][-1]['category'], 'abandoned')
                self.assertFalse(review['items'][-1]['eligible'])
                # Preserve the server's existing ValueError -> 400 JSON contract.
                self.assertEqual(request('/batches/999999/review')[0], 400)
                status, result = request(f'/batches/{batch_id}/register-published',
                                         {'draft_ids': ids, 'human_confirmed': True})
                self.assertEqual(status, 200)
                self.assertEqual((result['registered'], result['skipped']), (5, 1))
                self.assertEqual(result['results'][-1]['reason'], 'ARCHIVED')
                self.assertTrue(result['review']['review_complete'])
                self.assertEqual(result['remote_actions'], 0)
                _, repeat = request(f'/batches/{batch_id}/register-published',
                                    {'draft_ids': ids[:5], 'human_confirmed': True})
                self.assertEqual((repeat['registered'], repeat['skipped']), (0, 5))
                self.assertTrue(all(r['reason'] == 'ALREADY_PUBLISHED' for r in repeat['results']))
                tables = ('publisher_drafts', 'publisher_batches', 'publisher_media_assets',
                          'publisher_batch_executions', 'publisher_execution_items')
                before = {t: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {t} ORDER BY 1,2')] for t in tables}
                for action in ('hide', 'restore'):
                    status, result = request('/queue/presentation',
                                             {'action': action, 'target_type': 'BATCH', 'target_id': batch_id})
                    self.assertEqual(status, 200)
                    self.assertEqual(result['hidden'], action == 'hide')
                after = {t: [tuple(r) for r in self.conn.execute(f'SELECT * FROM {t} ORDER BY 1,2')] for t in tables}
                self.assertEqual(before, after)
                self.assertEqual(get_row(self.conn, 'publisher_drafts', abandoned)['status'], 'ARCHIVED')
                self.assertEqual(get_row(self.conn, 'publisher_media_assets', 6)['status'], 'AVAILABLE')
                self.assertFalse(api.worker.mock_calls)
                self.assertFalse(self.provider.deleted)
                self.assertFalse(self.provider.downloads)
            finally:
                server.shutdown()
                server.server_close()
                thread.join(5)

    def test_frontend_non_json_and_malformed_errors_keep_http_status(self):
        source = (Path(__file__).resolve().parents[1] / 'dashboard/publisher.js').read_text(encoding='utf-8')
        start = source.index('async function api(')
        end = source.index('\n}', start) + 2
        script = source[start:end] + """
const assert=require('node:assert/strict');
(async()=>{
 for(const [status,type,body] of [[404,'text/html','<!DOCTYPE html>'],[502,'application/json','broken']]){
  global.fetch=async()=>({status,ok:false,headers:{get:()=>type},json:async()=>JSON.parse(body)});
  await assert.rejects(api('/batches/9/review'),new RegExp('HTTP '+status));
 }
 global.fetch=async()=>({status:400,ok:false,headers:{get:()=> 'application/json'},json:async()=>({error:'Batch ausente'})});
 await assert.rejects(api('/batches/9/review'),e=>e.message==='Batch ausente'&&e.httpStatus===400);
 global.fetch=async()=>({status:200,ok:true,headers:{get:()=> 'application/json'},json:async()=>({batch_id:9})});
 assert.deepEqual(await api('/batches/9/review'),{batch_id:9});
})().catch(e=>{console.error(e);process.exitCode=1;});
"""
        result = subprocess.run(['node', '-e', script], capture_output=True, text=True, timeout=15, shell=False)
        self.assertEqual(result.returncode, 0, result.stderr)
