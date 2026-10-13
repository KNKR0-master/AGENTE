"""Opt-in UI regression, with fake HTTP and a disposable browser profile."""

import copy
import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from urllib.parse import urlsplit

from tests.test_publisher_queue_browser import QueueFixtureHandler, fixture


class ConsumptionFixtureHandler(QueueFixtureHandler):
    review = None

    def respond(self, body):
        payload = json.dumps(body).encode('utf-8')
        self.send_response(200)
        self.send_header('Content-Type', 'application/json; charset=utf-8')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        path = urlsplit(self.path).path
        if path == '/api/publisher/batches/24/review':
            self.respond(type(self).review)
        elif path == '/api/publisher/batches/24/consumption-evaluation':
            type(self).requests.append(('GET', path, None))
            self.respond(type(self).review['consumption'])
        else:
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append(('POST', path, body))
        review = type(self).review
        consumption = review['consumption']
        if path.endswith('/register-published'):
            for draft in type(self).snapshot['drafts'][:4]:
                draft.update(status='PUBLISHED', queue_display=dict(label='Publicado no Studio', kind='ready', message=''))
            for item in review['items'][:4]:
                item.update(status='PUBLISHED', category='published', eligible=False)
            for item in consumption['items'][:4]:
                item.update(category='eligible', blocker=None)
            review.update(prepared=0, published=4, eligible_ids=[], review_complete=True)
            consumption.update(published_count=4, counts=dict(eligible=4, consumed=0, blocked=0,
                                                             abandoned=1, pending=0, local_cleanup_pending=0))
            self.respond(dict(registered=4, skipped=0, requires_review=0, review=review))
        elif path.endswith('/finalize-consumption'):
            results = []
            for selection in body['items']:
                draft_id = selection['draft_id']
                item = consumption['items'][draft_id - 1]
                failed = draft_id == 2
                item.update(category='blocked' if failed else 'consumed')
                result = dict(draft_id=draft_id, result='FAILED' if failed else 'CONSUMED',
                              reason='CLEANUP_FAILED' if failed else None)
                item['last_result'] = result
                type(self).snapshot['drafts'][draft_id - 1]['media_status'] = 'CONSUMED_PENDING_DELETE' if failed else 'CONSUMED'
                results.append(result)
            consumption['counts'] = dict(eligible=0, consumed=3, blocked=1, abandoned=1, pending=0,
                                         local_cleanup_pending=0)
            self.respond(dict(status='PARTIAL', request_id=body['request_id'], results=results,
                              counts=dict(CONSUMED=3, ALREADY_CONSUMED=0, BLOCKED=0, FAILED=1,
                                          QUEUED=0, RUNNING=0), consumption=consumption))
        else:
            self.send_error(400, 'Unexpected mutation in fake batch consumption UI')


@unittest.skipUnless(os.environ.get('RUN_PUBLISHER_BATCH_CONSUMPTION_BROWSER') == '1',
                     'Prepared for separate opt-in functional validation')
class BatchConsumptionBrowserTests(unittest.TestCase):
    def test_registration_evaluation_separate_confirmation_and_partial_results(self):
        from playwright.sync_api import sync_playwright
        snapshot = fixture()
        snapshot['drafts'] = [dict(copy.deepcopy(snapshot['drafts'][0]), id=i, queue_position=i,
                                   filename=f'FINAL_{i}.mp4', status='ARCHIVED' if i == 5 else 'READY_FOR_REVIEW',
                                   persistence_status='PERSISTED', media_asset_id=100 + i)
                              for i in range(1, 6)]
        review = dict(batch_id=24, name='Lote de revisão', total=5, prepared=4, attention=0,
                      processing=0, queued=0, published=0, scheduled=0, abandoned=1,
                      prepared_percent=100, review_complete=False, eligible_ids=[1, 2, 3, 4],
                      items=[dict(draft_id=i, filename=f'FINAL_{i}.mp4',
                                  status='ARCHIVED' if i == 5 else 'READY_FOR_REVIEW',
                                  category='abandoned' if i == 5 else 'prepared', eligible=i != 5,
                                  blocker='ARCHIVED' if i == 5 else None) for i in range(1, 6)])
        review['consumption'] = dict(batch_id=24, published_count=0, pending_requests=[],
            counts=dict(eligible=0, consumed=0, blocked=4, abandoned=1, pending=0, local_cleanup_pending=0),
            items=[dict(draft_id=i, filename=f'FINAL_{i}.mp4', category='abandoned' if i == 5 else 'blocked',
                        blocker='ABANDONED_MEDIA_PROTECTED' if i == 5 else 'PUBLICATION_NOT_REGISTERED',
                        identity_token=str(i) * 64, last_result=None,
                        identity=dict(account=dict(name='Conta de teste'),
                                      media=dict(remote_file_id=f'fake-drive-{i}')))
                   for i in range(1, 6)])
        ConsumptionFixtureHandler.snapshot = snapshot
        ConsumptionFixtureHandler.review = review
        ConsumptionFixtureHandler.requests = []
        ConsumptionFixtureHandler.history_pages = None
        server = ThreadingHTTPServer(('127.0.0.1', 0), ConsumptionFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory(prefix='batch-consumption-ui-') as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    origin = f'http://127.0.0.1:{server.server_port}'

                    def isolate_network(route):
                        if route.request.url.startswith(origin + '/'):
                            route.continue_()
                        else:
                            route.abort()

                    page.route('**/*', isolate_network)
                    page.goto(origin + '/publisher.html')
                    page.locator('[data-batch-review="24"]').first.click()
                    page.locator('#batchPostingForm [name=human_confirmed]').check()
                    page.locator('#batchPostingForm [type=submit]').click()
                    page.wait_for_function("document.querySelector('#batchReviewResult').textContent.includes('4 registrados')")
                    self.assertFalse(any(path.endswith('/finalize-consumption') for _, path, _ in ConsumptionFixtureHandler.requests))
                    page.locator('#batchConsumptionEvaluate').click()
                    page.locator('#batchConsumptionPanel').wait_for(state='visible')
                    self.assertEqual(page.locator('[data-consumption-draft]:checked').count(), 4)
                    self.assertTrue(page.locator('[data-consumption-draft="5"]').is_disabled())
                    page.locator('#batchConsumptionForm [type=submit]').click()
                    self.assertFalse(any(path.endswith('/finalize-consumption') for _, path, _ in ConsumptionFixtureHandler.requests))
                    auth = page.locator('#batchConsumptionForm [name=drive_delete_confirmed]')
                    auth.check()
                    page.locator('[data-consumption-draft="1"]').uncheck()
                    self.assertFalse(auth.is_checked())
                    self.assertIn('Excluir 3 arquivo(s)', page.locator('#batchConsumptionSelection').inner_text())
                    page.locator('[data-consumption-draft="1"]').check()
                    auth.check()
                    page.locator('#batchConsumptionForm [type=submit]').click()
                    page.wait_for_function("document.querySelector('#batchConsumptionResult').textContent.includes('Finalização com pendências')")
                    calls = [body for _, path, body in ConsumptionFixtureHandler.requests if path.endswith('/finalize-consumption')]
                    self.assertEqual(len(calls), 1)
                    self.assertEqual(calls[0]['confirmed_count'], 4)
                    self.assertTrue(calls[0]['drive_delete_confirmed'])
                    self.assertEqual([item['draft_id'] for item in calls[0]['items']], [1, 2, 3, 4])
                    self.assertIn('1 falhas', page.locator('#batchConsumptionResult').inner_text())
                    self.assertIn('FINAL_2.mp4', page.locator('#batchConsumptionResult').inner_text())
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
            thread.join(5)


if __name__ == '__main__':
    unittest.main()
