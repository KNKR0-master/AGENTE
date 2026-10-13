import json
import shutil
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import urlsplit

from tests import test_publisher_queue_browser as fixture_module


class ClosureHandler(fixture_module.QueueFixtureHandler):
    def reply(self, body):
        payload = json.dumps(body).encode()
        self.send_response(200)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(payload)))
        self.end_headers()
        self.wfile.write(payload)

    def do_GET(self):
        if self.path == '/api/publisher/batches/24/review':
            self.reply(type(self).review)
        else:
            super().do_GET()

    def do_POST(self):
        path = urlsplit(self.path).path
        if path not in ('/api/publisher/resolve-draft', '/api/publisher/batches/24/register-published'):
            return super().do_POST()
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        type(self).requests.append(('POST', path, body))
        if path.endswith('register-published'):
            type(self).review['items'][0].update(eligible=False, category='published', status='PUBLISHED')
            type(self).review.update(prepared=0, published=1, eligible_ids=[])
            self.reply({'registered': 1, 'skipped': 0, 'requires_review': 1, 'review': type(self).review})
        else:
            self.reply({'status': 'MANUAL_ACTION_REQUIRED', 'remote_actions': 0})


@unittest.skipUnless(shutil.which('chrome') or Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe').exists(), 'Chrome required')
class FunctionalClosureBrowserTests(unittest.TestCase):
    def test_complete_empty_list_explains_current_state_without_release_or_retry(self):
        from playwright.sync_api import sync_playwright
        snapshot = fixture_module.fixture()
        ClosureHandler.snapshot, ClosureHandler.requests = snapshot, []
        draft = snapshot['drafts'][1]
        draft.update(status='MANUAL_ACTION_REQUIRED', upload_status='TRANSFER_UNKNOWN',
                     media_status='NEEDS_RECONCILIATION', media_asset_id=2, resolution_checkpoint_id=91,
                     queue_capabilities={'can_verify': True, 'can_resolve': True},
                     reconciliation_evidence={'id': 7, 'current': True, 'outcome': 'REMOTE_STATE_UNKNOWN',
                         'coverage_status': 'COMPLETE', 'listing_complete': True, 'current_draft_count': 0,
                         'match_result': 'NO_CURRENT_MATCH', 'blocker_code': None})
        server = ThreadingHTTPServer(('127.0.0.1', 0), ClosureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with sync_playwright() as p:
                browser = p.chromium.launch(channel='chrome', headless=True)
                try:
                    page = browser.new_page()
                    for local, expected in [('NOT_STARTED', 'Risco remoto aparente baixo'),
                                             ('POSSIBLE_REMOTE_EFFECT', 'O risco de duplicidade permanece')]:
                        draft['resolution_safety_summary'] = {'local_attempt': local}
                        page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                        page.locator('[data-resolve-draft="2"]').click()
                        form = page.locator('#resolutionForm')
                        text = form.inner_text()
                        self.assertIn('Nenhum rascunho atual encontrado', text)
                        self.assertIn('atualmente possui 0 rascunhos', text)
                        self.assertIn('Isso não confirma sozinho', text)
                        self.assertIn(expected, text)
                        self.assertNotIn('Não foi possível concluir a verificação', text)
                        self.assertTrue(form.locator('[value="CONFIRM_EXISTS"]').is_disabled())
                        form.locator('[value="ABANDON"]').check()
                        form.locator('[name="reason"]').fill('Decisao apenas na fixture')
                        form.locator('[name="human_confirmed"]').check()
                        form.locator('[type="submit"]').click()
                        self.assertIn('risco de duplicidade', page.locator('#resolutionResult').inner_text())
                        self.assertFalse([r for r in ClosureHandler.requests if r[0] == 'POST'])
                finally:
                    browser.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_human_resolution_and_batch_review_are_explicit_local_actions(self):
        from playwright.sync_api import sync_playwright
        snapshot = fixture_module.fixture()
        ClosureHandler.snapshot = snapshot
        ClosureHandler.requests = []
        first, ambiguous, _ = snapshot['drafts']
        first.update(status='READY_FOR_REVIEW', persistence_status='PERSISTED')
        ambiguous.update(status='MANUAL_ACTION_REQUIRED', upload_status='TRANSFER_UNKNOWN',
                         media_status='NEEDS_RECONCILIATION', media_asset_id=2, resolution_checkpoint_id=91,
                         queue_capabilities={'can_verify': True, 'can_resolve': True},
                         reconciliation_evidence={'id': 7, 'current': True, 'outcome': 'REMOTE_MATCH_AMBIGUOUS'})
        ClosureHandler.review = dict(batch_id=24, name='Lote de revisão', total=3, prepared=1, published=0,
            scheduled=0, abandoned=0, attention=1, queued=1, processing=0, prepared_percent=33,
            review_complete=False, eligible_ids=[1], items=[
                dict(draft_id=d['id'], filename=d['filename'], status=d['status'], category=category,
                     eligible=d['id']==1, blocker=None if d['id']==1 else 'PERSISTENCE_UNCONFIRMED')
                for d, category in zip(snapshot['drafts'], ('prepared', 'attention', 'queued'))])
        snapshot['batches'][0]['review_summary'] = {k:v for k,v in ClosureHandler.review.items() if k not in ('items','eligible_ids')}
        server = ThreadingHTTPServer(('127.0.0.1', 0), ClosureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-resolve-draft="2"]').click()
                    form = page.locator('#resolutionForm')
                    self.assertIn('Mais de um possível rascunho', form.inner_text())
                    form.locator('[value="ABANDON"]').check()
                    form.locator('[name="reason"]').fill('Conferido manualmente no Studio')
                    form.locator('[name="human_confirmed"]').check()
                    form.locator('[type="submit"]').click()
                    self.assertIn('Confirme a conferência', page.locator('#resolutionResult').inner_text())
                    self.assertFalse([r for r in ClosureHandler.requests if r[1].endswith('resolve-draft')])
                    form.locator('[value="UNKNOWN"]').check()
                    form.locator('[type="submit"]').click()
                    page.wait_for_function("!document.querySelector('#resolutionDialog').open")
                    resolution = next(r[2] for r in ClosureHandler.requests if r[1].endswith('resolve-draft'))
                    self.assertEqual(resolution['checkpoint_id'], 91)
                    self.assertEqual(resolution['decision'], 'UNKNOWN')
                    self.assertTrue(resolution['human_confirmed'])
                    page.locator('[data-batch-review="24"]').first.click()
                    dialog = page.locator('#batchReviewDialog')
                    dialog.wait_for(state='visible')
                    self.assertIn('1 requerem atenção', dialog.inner_text())
                    self.assertTrue(dialog.locator('[data-posting-draft="2"]').is_disabled())
                    self.assertTrue(dialog.locator('[data-posting-draft="3"]').is_disabled())
                    self.assertTrue(dialog.locator('[data-posting-draft="1"]').is_checked())
                    page.wait_for_timeout(5500)
                    self.assertTrue(dialog.is_visible())
                    dialog.locator('[name="human_confirmed"]').check()
                    dialog.locator('#batchPostingForm [type="submit"]').click()
                    page.wait_for_function("document.querySelector('#batchReviewResult').textContent.includes('1 registrados')")
                    posting = next(r[2] for r in ClosureHandler.requests if r[1].endswith('register-published'))
                    self.assertEqual(posting['draft_ids'], [1])
                    self.assertTrue(posting['human_confirmed'])
                    self.assertIn('Requer atenção', dialog.inner_text())
                    self.assertFalse(errors)
                    self.assertTrue(all(r[1] in ('/api/publisher/resolve-draft', '/api/publisher/batches/24/register-published') for r in ClosureHandler.requests if r[0]=='POST'))
                    evidence = fixture_module.ROOT / 'data/publisher/evidence'
                    evidence.mkdir(parents=True, exist_ok=True)
                    page.screenshot(path=str(evidence / 'functional-closure-batch-review.png'), full_page=True)
                    page.set_viewport_size({'width':390,'height':844})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 410)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
