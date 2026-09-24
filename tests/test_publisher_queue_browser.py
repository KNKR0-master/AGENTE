import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def fixture():
    caps = {'can_edit': True, 'can_reorder': True, 'can_remove': True,
            'can_start': True, 'can_verify': False, 'remote_effect_possible': False}
    drafts = []
    for index, (product_id, name, caption) in enumerate([
        (1, 'G1C3A1.mp4', 'Legenda do primeiro vídeo'),
        (2, 'G1C4A1.mp4', 'Legenda específica do segundo vídeo'),
        (1, 'G2C3A2.mp4', 'Legenda do terceiro vídeo'),
    ], 1):
        drafts.append({'id': index, 'batch_id': 24, 'queue_position': index,
                       'status': 'DRAFT', 'filename': name, 'product_id': product_id,
                       'product_name': 'Body Splash' if product_id == 1 else 'Escova de Dentes',
                       'product_short_name': 'Body Splash' if product_id == 1 else 'Escova',
                       'account_id': 1, 'account_name': 'CECILIA FIGUEIREDO',
                       'caption': caption, 'checklist': {}, 'media_status': 'RESERVED',
                       'queue_capabilities': caps, 'queue_display': {'label': 'Pendente', 'kind': 'neutral', 'message': ''},
                       'upload_status': 'NOT_STARTED', 'persistence_status': 'NOT_PERSISTED'})
    return {'drafts': drafts, 'products': [
        {'id': 1, 'name': 'Body Splash', 'short_name': 'Body Splash', 'active': True, 'aliases': [], 'folders': []},
        {'id': 2, 'name': 'Escova de Dentes', 'short_name': 'Escova', 'active': True, 'aliases': [], 'folders': []}],
        'accounts': [{'id': 1, 'name': 'CECILIA FIGUEIREDO', 'active': True}],
        'batches': [{'id': 24, 'name': 'Lote de revisão'}], 'presets': [], 'logs': [],
        'statuses': ['DRAFT', 'ARCHIVED'], 'media_assets': [], 'drive_folders': [],
        'account_protection': [], 'executions': [], 'execution_items': [],
        'queue_preflights': {24: {'batch_id': 24, 'total_items': 3, 'ready_items': 2,
                                  'blocked_items': 1, 'warning_items': 0,
                                  'items': [{'draft_id': i, 'ready': i != 2,
                                             'blockers': ['CAPTION_MISSING'] if i == 2 else [],
                                             'warnings': []} for i in (1, 2, 3)]}},
        'worker_config': {'persistence_workflow_version': 1, 'tiktok_actions_paused': False},
        'preferences': {}, 'system_status': {'system_label': 'Sistema pronto',
                                            'account_label': 'Cecília conectada',
                                            'drive_label': 'Drive conectado'}}


class QueueFixtureHandler(SimpleHTTPRequestHandler):
    snapshot = fixture()
    reject_reorder = False
    last_reorder = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dashboard'), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        if self.path.startswith('/api/publisher'):
            if self.path.startswith('/api/publisher/library'):
                body = {'items': [], 'page': 1, 'pages': 1, 'total': 0}
            elif self.path.startswith('/api/publisher/drive-sync/status'):
                body = {'cursor_status': 'READY'}
            else:
                body = self.snapshot
            data = json.dumps(body).encode('utf-8')
            self.send_response(200)
            self.send_header('Content-Type', 'application/json; charset=utf-8')
            self.send_header('Content-Length', str(len(data)))
            self.end_headers()
            self.wfile.write(data)
            return
        super().do_GET()

    def do_POST(self):
        match = re.fullmatch(r'/api/publisher/batches/(\d+)/reorder', self.path)
        if not match:
            self.send_error(405)
            return
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        ids = body['ordered_draft_ids']
        type(self).last_reorder = ids
        members = [d['id'] for d in self.snapshot['drafts'] if d['batch_id'] == int(match[1])]
        valid = not self.reject_reorder and len(ids) == len(members) and set(ids) == set(members)
        if valid:
            for position, draft_id in enumerate(ids, 1):
                next(d for d in self.snapshot['drafts'] if d['id'] == draft_id)['queue_position'] = position
        data = json.dumps({'ordered_draft_ids': ids} if valid else {'error': 'BATCH_ALREADY_EXECUTED'}).encode()
        self.send_response(200 if valid else 400)
        self.send_header('Content-Type', 'application/json')
        self.send_header('Content-Length', str(len(data)))
        self.end_headers()
        self.wfile.write(data)


@unittest.skipUnless(shutil.which('chrome') or Path(r'C:\Program Files\Google\Chrome\Application\chrome.exe').exists(),
                     'Chrome Stable is required for visual queue checks')
class QueueBrowserTests(unittest.TestCase):
    def setUp(self):
        QueueFixtureHandler.snapshot = fixture()
        QueueFixtureHandler.reject_reorder = False
        QueueFixtureHandler.last_reorder = None

    def test_reorder_backend_confirmation_and_rejection(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('#queueBatches .queue-item').first.wait_for(timeout=10000)
                    page.locator('[data-draft="2"][data-move="up"]').click()
                    page.wait_for_function("document.querySelector('#queueBatches .queue-item')?.dataset.queueDraft === '2'")
                    self.assertEqual(QueueFixtureHandler.last_reorder, [2, 1, 3])
                    page.reload()
                    page.locator('#queueBatches .queue-item').first.wait_for(timeout=10000)
                    self.assertEqual(page.locator('#queueBatches .queue-item').first.get_attribute('data-queue-draft'), '2')
                    page.locator('[data-queue-draft="3"]').drag_to(page.locator('[data-queue-draft="2"]'))
                    page.wait_for_function("document.querySelector('#queueBatches .queue-item')?.dataset.queueDraft === '3'")
                    self.assertEqual(QueueFixtureHandler.last_reorder, [3, 2, 1])
                    QueueFixtureHandler.reject_reorder = True
                    page.locator('[data-draft="3"][data-move="down"]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('BATCH_ALREADY_EXECUTED')")
                    self.assertEqual(page.locator('#queueBatches .queue-item').first.get_attribute('data-queue-draft'), '3')
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    @unittest.skipUnless(os.environ.get('PUBLISHER_QUEUE_OPERATIONAL_SMOKE') == '1',
                         'operational SQLite snapshot is opt-in')
    def test_operational_snapshot_read_only(self):
        from playwright.sync_api import sync_playwright
        from src.publisher.service import PublisherService

        db = ROOT / 'data' / 'agent.db'
        with sqlite3.connect(db.resolve().as_uri() + '?mode=ro', uri=True) as conn:
            conn.row_factory = sqlite3.Row
            snapshot = PublisherService(conn, ROOT).snapshot()
        self.assertTrue(snapshot['drafts'])
        snapshot.update({'worker_config': {'persistence_workflow_version': 1, 'tiktok_actions_paused': False},
                         'system_status': {'system_label': 'Dados locais', 'account_label': 'Somente leitura',
                                           'drive_label': 'Sem consulta remota'}})
        previous = QueueFixtureHandler.snapshot
        QueueFixtureHandler.snapshot = snapshot
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('#queueBatches .queue-batch').first.wait_for(timeout=10000)
                    self.assertFalse(errors, errors)
                    self.assertEqual(page.locator('#queueBatches .queue-batch').count(),
                                     len({d['batch_id'] for d in snapshot['drafts'] if d['status'] != 'ARCHIVED'}))
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(),
                                     len([d for d in snapshot['drafts'] if d['status'] != 'ARCHIVED']))
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
            QueueFixtureHandler.snapshot = previous

    def test_isolated_chrome_queue(self):
        from playwright.sync_api import sync_playwright

        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('#queueBatches .queue-item').first.wait_for(timeout=10000)
                    self.assertFalse(errors, errors)
                    self.assertEqual(page.locator('#queueBatches .queue-batch').count(), 1)
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(), 3)
                    self.assertTrue(page.locator('[data-start-batch="24"]').is_disabled())
                    self.assertIn('Legenda específica do segundo vídeo', page.locator('#queueBatches').inner_text())
                    page.evaluate("""() => {
                      const preflight=state.queue_preflights[24];
                      preflight.blocked_items=0;preflight.ready_items=3;preflight.warning_items=1;
                      preflight.items.forEach(item=>{item.ready=true;item.blockers=[];});
                      preflight.items[0].warnings=['REVIEW_RECOMMENDED'];
                      renderQueue();
                    }""")
                    self.assertTrue(page.locator('[data-start-batch="24"]').is_enabled())
                    page.evaluate("""() => {state.account_protection=[{blocked:true,state:'RATE_LIMITED'}];renderQueue();}""")
                    self.assertTrue(page.locator('[data-start-batch="24"]').is_disabled())
                    page.evaluate("""() => {state.account_protection=[];renderQueue();}""")
                    page.evaluate("""() => {
                      const draft=state.drafts[0];
                      draft.upload_status='TRANSFER_UNKNOWN';
                      draft.queue_display={label:'Envio a verificar',kind:'attention',message:'O TikTok pode já ter recebido este vídeo. Não envie novamente.'};
                      draft.queue_capabilities={can_edit:false,can_remove:false,can_reorder:false,can_verify:true,remote_effect_possible:true};
                      renderQueue();
                    }""")
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-remove-draft]').count(), 0)
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-edit]').count(), 0)
                    page.locator('[data-queue-draft="1"] [data-review]').click()
                    self.assertTrue(page.locator('#reviewDialog').is_visible())
                    self.assertEqual(page.locator('#reviewDialog [data-complete]').count(), 0)
                    page.locator('#reviewDialog [data-close]').click()
                    screenshot = os.environ.get('PUBLISHER_QUEUE_SCREENSHOT')
                    if screenshot:
                        page.screenshot(path=screenshot, full_page=True)
                    page.locator('#queueSearch').fill('G2C3A2')
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(), 1)
                    self.assertEqual([d['queue_position'] for d in QueueFixtureHandler.snapshot['drafts']], [1, 2, 3])
                    page.set_viewport_size({'width': 390, 'height': 844})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 410)
                    if screenshot:
                        page.screenshot(path=str(Path(screenshot).with_name('queue-mobile.png')), full_page=True)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()


if __name__ == '__main__':
    unittest.main()
