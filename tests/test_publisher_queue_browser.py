import json
import os
import re
import shutil
import sqlite3
import tempfile
import threading
import unittest
from urllib.parse import parse_qs, urlsplit
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
        'accounts': [{'id': 1, 'name': 'CECILIA FIGUEIREDO', 'active': True},
                     {'id': 2, 'name': 'Conta alternativa', 'active': True}],
        'batches': [{'id': 24, 'name': 'Lote de revisão', 'queue_capabilities': {
            'can_rename': True, 'can_edit_configuration': True,
            'editable_fields': ['account_id', 'preset_id'], 'can_reorder': True,
            'can_cancel': True, 'active_draft_count': 3,
        }}], 'presets': [{'id': 31, 'name': 'Preset alternativo', 'account_id': 2}], 'logs': [],
        'statuses': ['DRAFT', 'ARCHIVED'], 'media_assets': [], 'drive_folders': [],
        'account_protection': [], 'executions': [], 'execution_items': [],
        'history': {'executions': [], 'total_count': 0, 'has_more': False, 'next_before_id': None},
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
    reject_cancel = False
    reject_configuration = False
    last_reorder = None
    last_rename = None
    last_configuration = None
    last_cancel = None
    requests = []
    history_pages = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(ROOT / 'dashboard'), **kwargs)

    def log_message(self, *_args):
        pass

    def do_GET(self):
        parsed = urlsplit(self.path)
        if parsed.path.startswith('/api/publisher'):
            if parsed.path.startswith('/api/publisher/library'):
                body = {'items': [], 'page': 1, 'pages': 1, 'total': 0}
            elif parsed.path.startswith('/api/publisher/drive-sync/status'):
                body = {'cursor_status': 'READY'}
            elif parsed.path == '/api/publisher/execution-history':
                query = parse_qs(parsed.query)
                before_id = query.get('before_id', [None])[0]
                type(self).requests.append(('GET', parsed.path, query))
                pages = type(self).history_pages
                body = pages.get(before_id, pages.get(None)) if pages else None
                if body is None:
                    body = self.snapshot.get('history', {'executions': [], 'total_count': 0, 'has_more': False})
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
        body = json.loads(self.rfile.read(int(self.headers['Content-Length'])))
        path = urlsplit(self.path).path
        type(self).requests.append(('POST', path, body))
        reorder = re.fullmatch(r'/api/publisher/batches/(\d+)/reorder', path)
        rename = re.fullmatch(r'/api/publisher/batches/(\d+)/rename', path)
        config = re.fullmatch(r'/api/publisher/batches/(\d+)/configuration', path)
        cancel = re.fullmatch(r'/api/publisher/batches/(\d+)/cancel', path)
        if reorder:
            ids = body['ordered_draft_ids']
            type(self).last_reorder = ids
            members = [d['id'] for d in self.snapshot['drafts'] if d['batch_id'] == int(reorder[1])]
            valid = not self.reject_reorder and len(ids) == len(members) and set(ids) == set(members)
            if valid:
                for position, draft_id in enumerate(ids, 1):
                    next(d for d in self.snapshot['drafts'] if d['id'] == draft_id)['queue_position'] = position
            result, status = ({'ordered_draft_ids': ids}, 200) if valid else ({'error': 'BATCH_ALREADY_EXECUTED'}, 400)
        elif rename:
            batch = next(item for item in self.snapshot['batches'] if item['id'] == int(rename[1]))
            batch['name'] = body['name']
            type(self).last_rename = (int(rename[1]), body)
            result, status = ({'batch_id': batch['id'], 'name': batch['name']}, 200)
        elif config:
            type(self).last_configuration = (int(config[1]), body)
            if type(self).reject_configuration:
                result, status = ({'error': 'BATCH_CONFIGURATION_EDIT_BLOCKED'}, 409)
            else:
                account = next((item for item in self.snapshot['accounts'] if item['id'] == body.get('account_id')), None)
                for draft in self.snapshot['drafts']:
                    if draft['batch_id'] == int(config[1]):
                        draft.update(body)
                        if account:
                            draft['account_name'] = account['name']
                result, status = ({'batch_id': int(config[1]), 'changed_fields': list(body)}, 200)
        elif cancel:
            type(self).last_cancel = (int(cancel[1]), body)
            if type(self).reject_cancel:
                batch = next(item for item in self.snapshot['batches'] if item['id'] == int(cancel[1]))
                batch['queue_capabilities']['can_cancel'] = False
                result, status = ({'error': 'BATCH_CANCEL_BLOCKED'}, 409)
            else:
                for draft in self.snapshot['drafts']:
                    if draft['batch_id'] == int(cancel[1]):
                        draft['status'] = 'ARCHIVED'
                batch = next(item for item in self.snapshot['batches'] if item['id'] == int(cancel[1]))
                batch['queue_capabilities']['can_cancel'] = False
                result, status = ({'batch_id': int(cancel[1]), 'cancelled_drafts': 3,
                                   'reservations_released': 3, 'audit_preserved': True}, 200)
        else:
            self.send_error(405)
            return
        data = json.dumps(result).encode()
        self.send_response(status)
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
        QueueFixtureHandler.reject_cancel = False
        QueueFixtureHandler.reject_configuration = False
        QueueFixtureHandler.last_reorder = None
        QueueFixtureHandler.last_rename = None
        QueueFixtureHandler.last_configuration = None
        QueueFixtureHandler.last_cancel = None
        QueueFixtureHandler.requests = []
        QueueFixtureHandler.history_pages = None

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
                      state.batches[0].queue_capabilities={can_rename:false,can_edit:false,can_edit_configuration:false,can_reorder:false,can_remove:false,can_remove_item:false,can_cancel:false};
                      renderQueue();
                    }""")
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-remove-draft]').count(), 0)
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-edit]').count(), 0)
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-cleanup]').count(), 0)
                    self.assertEqual(page.locator('[data-batch-cancel]').count(), 0)
                    self.assertIn('Verificar status', page.locator('[data-queue-draft="1"]').inner_text())
                    page.locator('[data-queue-draft="1"] [data-review]').click()
                    self.assertTrue(page.locator('#reviewDialog').is_visible())
                    self.assertEqual(page.locator('#reviewDialog [data-complete]').count(), 0)
                    page.locator('#reviewDialog [data-close]').click()
                    page.evaluate("""() => {
                      const draft=state.drafts[0];
                      draft.status='MANUAL_ACTION_REQUIRED';draft.media_status='NEEDS_RECONCILIATION';
                      draft.queue_display={label:'Requer verificação',kind:'attention',message:'Confirme o resultado antes de qualquer nova ação.'};
                      renderQueue();
                    }""")
                    self.assertIn('Verificar / Reconciliar', page.locator('[data-queue-draft="1"]').inner_text())
                    self.assertNotIn('Retry', page.locator('[data-queue-draft="1"]').inner_text())
                    self.assertEqual(page.locator('[data-queue-draft="1"] [data-remove-draft]').count(), 0)
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

    def test_history_is_read_only_collapsed_and_refresh_preserves_queue_interaction(self):
        from playwright.sync_api import sync_playwright

        snapshot = fixture()
        snapshot['history'] = {
            'total_count': 2, 'has_more': False, 'next_before_id': None,
            'executions': [
                {'id': 501, 'batch_id': 24, 'batch_names': ['Lote anterior'], 'status': 'COMPLETED',
                 'started_at': '2026-09-01T10:00:00Z', 'finished_at': '2026-09-01T10:01:00Z',
                 'total': 1, 'succeeded': 1, 'failed': 0, 'waiting_manual': 0,
                 'items': [{'execution_id': 501, 'draft_id': 1, 'execution_position': 1,
                            'item_status': 'SUCCEEDED', 'filename': 'video.mp4', 'draft_status': 'READY_FOR_REVIEW',
                            'batch_name': 'Lote anterior', 'product_name': 'Body Splash'}]},
                {'id': 500, 'batch_id': 24, 'batch_names': ['Lote anterior'], 'status': 'COMPLETED',
                 'started_at': '2026-08-31T10:00:00Z', 'finished_at': '2026-08-31T10:01:00Z',
                 'total': 1, 'succeeded': 1, 'failed': 0, 'waiting_manual': 0,
                 'items': [{'execution_id': 500, 'draft_id': 1, 'execution_position': 1,
                            'item_status': 'SUCCEEDED', 'filename': 'video.mp4', 'draft_status': 'READY_FOR_REVIEW',
                            'batch_name': 'Lote anterior', 'product_name': 'Body Splash'}]},
            ],
        }
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
                    page.locator('#queueBatches [data-queue-draft="1"]').wait_for(timeout=10000)
                    self.assertFalse(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertIn('Histórico (2)', page.locator('#queueHistoryLabel').inner_text())
                    page.locator('#queueHistory > summary').click()
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-item-key="501:1"]').count(), 1)
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-item-key="500:1"]').count(), 1)
                    self.assertEqual(page.locator('#queueHistoryEntries button').count(), 0)
                    page.locator('#queueHistoryEntries [data-history-execution="501"] > summary').click()

                    page.locator('#queueBatches [data-batch-rename="24"]').click()
                    editor = page.locator('#queueBatches [data-batch-rename-input]')
                    editor.fill('Nome digitado durante atualização')
                    page.wait_for_timeout(6200)
                    self.assertTrue(editor.is_visible())
                    self.assertEqual(editor.input_value(), 'Nome digitado durante atualização')
                    self.assertTrue(editor.evaluate('(node) => document.activeElement === node'))
                    self.assertTrue(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertTrue(page.locator('#queueHistoryEntries [data-history-execution="501"]').evaluate('(node) => node.open'))
                    self.assertFalse(errors, errors)
                    page.locator('#queueBatches [data-batch-rename-form] button[type="submit"]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('Lote renomeado')")
                    page.wait_for_function("document.querySelector('#queueBatches .queue-batch-title h3')?.textContent.includes('Nome digitado durante')")
                    self.assertEqual(QueueFixtureHandler.last_rename,
                                     (24, {'name': 'Nome digitado durante atualização'}))
                    self.assertTrue(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertTrue(page.locator('#queueHistoryEntries [data-history-execution="501"]').evaluate('(node) => node.open'))
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
            QueueFixtureHandler.snapshot = previous

    def test_pending_batch_configuration_browser_flow_uses_only_supported_fields(self):
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
                    page.locator('#queueBatches [data-batch-configure="24"]').wait_for(timeout=10000)
                    page.locator('#queueBatches [data-batch-configure="24"]').click()
                    dialog = page.locator('#batchConfigurationDialog')
                    self.assertTrue(dialog.is_visible())
                    self.assertEqual(page.locator('#batchConfigurationForm [name]').evaluate_all(
                        "nodes => nodes.map(node => node.name).sort()"), ['account_id', 'preset_id'])
                    page.locator('#batchConfigurationForm [name="account_id"]').select_option('2')
                    page.locator('#batchConfigurationForm [name="preset_id"]').select_option('31')
                    page.locator('#batchConfigurationForm button[type="submit"]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('Conta/preset atualizados')")
                    self.assertEqual(QueueFixtureHandler.last_configuration,
                                     (24, {'account_id': 2, 'preset_id': 31}))
                    self.assertEqual({draft['account_id'] for draft in QueueFixtureHandler.snapshot['drafts']}, {2})
                    self.assertEqual({draft['preset_id'] for draft in QueueFixtureHandler.snapshot['drafts']}, {31})
                    self.assertEqual({draft['caption'] for draft in QueueFixtureHandler.snapshot['drafts']}, {
                        'Legenda do primeiro vídeo', 'Legenda específica do segundo vídeo', 'Legenda do terceiro vídeo'})
                    self.assertEqual({draft['product_id'] for draft in QueueFixtureHandler.snapshot['drafts']}, {1, 2})
                    self.assertIn('Conta alternativa', page.locator('#queueBatches').inner_text())
                    self.assertFalse(dialog.is_visible())
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_cancel_confirmation_decline_and_confirmed_request(self):
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
                    page.locator('#queueBatches [data-batch-cancel="24"]').wait_for(timeout=10000)
                    dialogs = []
                    page.once('dialog', lambda dialog: (dialogs.append(dialog.message), dialog.dismiss()))
                    page.locator('#queueBatches [data-batch-cancel="24"]').click()
                    self.assertEqual(len(dialogs), 1)
                    self.assertIn('3 Draft(s) sairão da fila', dialogs[0])
                    self.assertIn('Nenhum arquivo remoto será excluído', dialogs[0])
                    self.assertIsNone(QueueFixtureHandler.last_cancel)
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(), 3)

                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#queueBatches [data-batch-cancel="24"]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('arquivados')")
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(), 0)
                    self.assertEqual(QueueFixtureHandler.last_cancel, (24, {'human_confirmed': True}))
                    self.assertEqual([draft['status'] for draft in QueueFixtureHandler.snapshot['drafts']],
                                     ['ARCHIVED', 'ARCHIVED', 'ARCHIVED'])
                    self.assertIn('arquivados', page.locator('#notice').inner_text())
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_cancel_rejection_refreshes_capabilities_without_hiding_batch(self):
        from playwright.sync_api import sync_playwright

        QueueFixtureHandler.reject_cancel = True
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('#queueBatches [data-batch-cancel="24"]').wait_for(timeout=10000)
                    page.once('dialog', lambda dialog: dialog.accept())
                    page.locator('#queueBatches [data-batch-cancel="24"]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('A fila foi atualizada')")
                    self.assertEqual(QueueFixtureHandler.last_cancel, (24, {'human_confirmed': True}))
                    self.assertEqual(page.locator('#queueBatches [data-queue-batch]').count(), 1)
                    self.assertEqual(page.locator('#queueBatches .queue-item').count(), 3)
                    self.assertEqual(page.locator('#queueBatches [data-batch-cancel]').count(), 0)
                    self.assertTrue(all(draft['status'] == 'DRAFT' for draft in QueueFixtureHandler.snapshot['drafts']))
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_history_pagination_appends_unique_execution_draft_pairs(self):
        from playwright.sync_api import sync_playwright

        def execution(execution_id):
            return {'id': execution_id, 'batch_id': 24, 'batch_names': ['Lote anterior'],
                    'status': 'COMPLETED', 'started_at': '2026-09-01T10:00:00Z',
                    'finished_at': '2026-09-01T10:01:00Z', 'total': 1, 'succeeded': 1,
                    'failed': 0, 'waiting_manual': 0,
                    'items': [{'execution_id': execution_id, 'draft_id': 1, 'execution_position': 1,
                               'item_status': 'SUCCEEDED', 'filename': 'video.mp4',
                               'draft_status': 'READY_FOR_REVIEW', 'batch_name': 'Lote anterior',
                               'product_name': 'Body Splash'}]}

        first = {'total_count': 35, 'has_more': True, 'next_before_id': 1006,
                 'executions': [execution(value) for value in range(1035, 1005, -1)]}
        second = {'total_count': 35, 'has_more': False, 'next_before_id': None,
                  'executions': [execution(value) for value in range(1006, 1000, -1)]}
        QueueFixtureHandler.snapshot = fixture()
        QueueFixtureHandler.snapshot['history'] = first
        QueueFixtureHandler.history_pages = {None: first, '1006': second}
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0] if context.pages else context.new_page()
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('#queueHistoryLabel').wait_for(timeout=10000)
                    self.assertFalse(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-execution]').count(), 30)
                    page.locator('#queueHistory > summary').click()
                    page.locator('#queueHistoryEntries [data-history-execution="1035"] > summary').click()
                    page.locator('#queueHistoryLoadMore').click()
                    page.wait_for_function("document.querySelectorAll('#queueHistoryEntries [data-history-execution]').length === 35")
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-item-key]').count(), 35)
                    keys = page.locator('#queueHistoryEntries [data-history-item-key]').evaluate_all(
                        "nodes => nodes.map(node => node.dataset.historyItemKey)")
                    self.assertEqual(len(keys), len(set(keys)))
                    self.assertEqual(QueueFixtureHandler.requests[-1][2].get('before_id'), ['1006'])
                    self.assertTrue(page.locator('#queueHistoryEntries [data-history-execution="1035"]').evaluate(
                        '(node) => node.open'))
                    self.assertTrue(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertFalse(page.locator('#queueHistoryLoadMore').is_visible())
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()
            QueueFixtureHandler.history_pages = None


if __name__ == '__main__':
    unittest.main()
