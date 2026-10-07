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
        'worker_config': {'persistence_workflow_version': 1, 'tiktok_actions_paused': False,
                          'max_drafts_per_batch': 30},
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
        if path == '/api/publisher/queue/presentation':
            presentation = self.snapshot.setdefault('queue_presentation', {'targets': [], 'history': []})
            targets = presentation['targets']
            existing = next((row for row in targets if row['target_type'] == body['target_type'] and row['target_id'] == body['target_id']), None)
            if body['action'] == 'hide' and existing is None:
                targets.append({'target_type': body['target_type'], 'target_id': body['target_id'], 'hidden_at': '2026-10-05T10:00:00Z'})
            elif body['action'] == 'restore' and existing is not None:
                targets.remove(existing)
            result, status = ({'hidden': body['action'] == 'hide'}, 200)
        elif reorder:
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
        elif path == '/api/publisher/verify-reconciliation':
            result, status = ({'outcome': 'REMOTE_STATE_UNKNOWN', 'remote_read_only': True,
                               'local_evidence_append': True}, 200)
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
    def test_local_headline_failure_is_not_remote_uncertainty(self):
        from playwright.sync_api import sync_playwright
        from src.publisher.queue_policy import draft_queue_presentation

        snapshot = QueueFixtureHandler.snapshot
        draft = snapshot['drafts'][0]
        draft.update(status='DRAFT', media_status='AVAILABLE', persistence_status='UNCONFIRMED')
        draft['queue_capabilities'] = {'remote_effect_possible': False}
        draft['queue_display'] = draft_queue_presentation(draft, local_preparation_error='HEADLINE_OUTPUT_CADENCE_INVALID')
        snapshot['drafts'] = [draft]
        snapshot['executions'] = [{'id': 11, 'batch_id': 24, 'status': 'PAUSED', 'total': 1}]
        snapshot['execution_items'] = [{'execution_id': 11, 'draft_id': draft['id'],
            'execution_position': 1, 'status': 'WAITING_MANUAL', 'error_code': 'MANUAL_ACTION_REQUIRED'}]
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.new_page()
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    item = page.locator(f'[data-queue-draft="{draft["id"]}"]')
                    item.wait_for()
                    self.assertIn('Falha na prepara\u00e7\u00e3o da headline', item.inner_text())
                    self.assertNotIn('Persist\u00eancia n\u00e3o confirmada', item.inner_text())
                    self.assertNotIn('Verifica\u00e7\u00e3o manual necess\u00e1ria', page.locator('.queue-batch-warning').inner_text())
                    details = page.locator('summary', has_text='Detalhes t\u00e9cnicos da prepara\u00e7\u00e3o')
                    details.click()
                    self.assertIn('HEADLINE_OUTPUT_CADENCE_INVALID', details.locator('..').inner_text())
                    self.assertEqual([request for request in QueueFixtureHandler.requests if request[0] == 'POST'], [])
                    page.screenshot(path=str(ROOT / 'data/publisher/qa/h3b-local-preparation-queue.png'), full_page=True)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

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

    def test_operational_identity_safe_thumbnail_and_history_rendering(self):
        from playwright.sync_api import sync_playwright

        snapshot = QueueFixtureHandler.snapshot
        draft = snapshot['drafts'][0]
        draft.update({'filename': 'Long video ' * 35 + '.mp4', 'batch_name': 'Perfumes 05/10',
                      'folder_name': 'Perfumes', 'remote_file_id': 'safe_file',
                      'thumbnail_link': 'https://untrusted.example/image',
                      'queue_display': {'label': 'Ação manual necessária', 'kind': 'attention',
                                        'message': '', 'persistence_label': 'Persistência não confirmada'}})
        snapshot['drafts'][1].update({'filename': None, 'product_name': None, 'product_short_name': None})
        snapshot['history'] = {'executions': [{'id': 80, 'batch_id': 24, 'batch_names': ['Perfumes 05/10'],
            'total': 1, 'succeeded': 1, 'items': [{**draft, 'draft_id': 1, 'execution_id': 80,
                'item_status': 'FAILED', 'draft_status': 'MANUAL_ACTION_REQUIRED'}]},
            {'id': 79, 'batch_id': 24, 'total': 1, 'items': [{**draft, 'draft_id': 1, 'execution_id': 79}]}],
            'total_count': 2, 'has_more': False, 'next_before_id': None}
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors, thumbnail_requests, remote_requests = [], [], []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.on('request', lambda request: remote_requests.append(request.url) if 'untrusted.example' in request.url else None)
                    def missing_thumbnail(route):
                        thumbnail_requests.append(route.request.url)
                        route.fulfill(status=404, body='')
                    page.route('**/api/publisher/library/thumbnail/**', missing_thumbnail)
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    row = page.locator('[data-queue-draft="1"]')
                    row.wait_for()
                    self.assertIn('Perfumes 05/10', row.inner_text())
                    self.assertIn('Perfumes', row.inner_text())
                    self.assertIn('Persistência não confirmada', row.inner_text())
                    self.assertIn('queue-attention', row.get_attribute('class'))
                    page.wait_for_function("document.querySelector('[data-queue-draft=\"1\"] img')?.hidden === true")
                    self.assertTrue(row.locator('.queue-placeholder').is_visible())
                    self.assertIn('Produto não atribuído', page.locator('[data-queue-draft="2"]').inner_text())
                    self.assertIn('Vídeo do Draft 2', page.locator('[data-queue-draft="2"]').inner_text())
                    page.locator('[data-queue-view="HISTORY"]').click()
                    page.locator('[data-history-execution="80"] > summary').click()
                    history = page.locator('[data-history-item-key="80:1"]')
                    self.assertTrue(history.is_visible())
                    self.assertEqual(page.locator('[data-history-item-key="79:1"]').count(), 1)
                    self.assertIn('Perfumes 05/10', history.inner_text())
                    self.assertTrue(thumbnail_requests)
                    self.assertFalse(remote_requests)
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

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

    def test_hide_restore_attention_filters_and_refresh_are_presentation_only(self):
        from playwright.sync_api import sync_playwright

        snapshot = QueueFixtureHandler.snapshot
        snapshot['drafts'][1].update({'status': 'MANUAL_ACTION_REQUIRED', 'upload_status': 'TRANSFER_UNKNOWN',
            'queue_display': {'label': 'Envio a verificar', 'kind': 'attention', 'message': 'Não envie novamente.'}})
        original = json.dumps({key: snapshot[key] for key in ('drafts', 'batches', 'media_assets')}, sort_keys=True)
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-queue-draft="1"]').wait_for()
                    page.locator('[data-queue-draft="1"] [data-queue-presentation][data-target-type="DRAFT"]').click()
                    page.wait_for_function("!document.querySelector('#queueBatches [data-queue-draft=\"1\"]')")
                    page.locator('[data-queue-presentation="hide"][data-target-type="BATCH"]').click()
                    page.wait_for_function("document.querySelectorAll('#queueBatches [data-queue-draft]').length===0")
                    page.locator('[data-queue-view="HIDDEN"]').click()
                    self.assertIn('1 requerem atenção', page.locator('[data-queue-view="HIDDEN"]').inner_text())
                    self.assertIn('Requer atenção', page.locator('[data-hidden-draft="2"]').inner_text())
                    page.locator('[data-hidden-draft="2"] [data-queue-presentation="restore"][data-target-type="BATCH"]').click()
                    page.wait_for_function("document.querySelectorAll('[data-hidden-draft]').length===1")
                    self.assertEqual(QueueFixtureHandler.snapshot['queue_presentation']['targets'][0]['target_type'], 'DRAFT')
                    page.locator('[data-queue-view="ACTIVE"]').click()
                    page.locator('[data-queue-draft="2"] [data-queue-presentation][data-target-type="DRAFT"]').click()
                    page.wait_for_function("!document.querySelector('#queueBatches [data-queue-draft=\"2\"]')")
                    page.locator('[data-queue-view="HIDDEN"]').click()
                    page.locator('#queueSearch').fill('G1C4A1')
                    page.locator('#filter-attention').select_option('REQUIRED')
                    page.locator('[data-hidden-draft="2"] details').evaluate('(node)=>{node.open=true;}')
                    page.wait_for_timeout(11200)
                    self.assertEqual(page.locator('[data-queue-view="HIDDEN"]').get_attribute('aria-selected'), 'true')
                    self.assertEqual(page.locator('#queueSearch').input_value(), 'G1C4A1')
                    self.assertEqual(page.locator('#filter-attention').input_value(), 'REQUIRED')
                    self.assertEqual(page.locator('[data-hidden-draft]').count(), 1)
                    self.assertIn('Requer atenção', page.locator('[data-hidden-draft="2"]').inner_text())
                    self.assertTrue(page.locator('[data-hidden-draft="2"] details').evaluate('(node)=>node.open'))
                    page.locator('#resetQueueFilters').click()
                    self.assertEqual(page.locator('[data-hidden-draft]').count(), 2)
                    for draft_id in (1, 2):
                        page.locator(f'[data-hidden-draft="{draft_id}"] [data-queue-presentation="restore"]').click()
                        page.wait_for_function(f"!document.querySelector('[data-hidden-draft=\"{draft_id}\"]')")
                    self.assertEqual(QueueFixtureHandler.snapshot['queue_presentation']['targets'], [])
                    self.assertEqual(original, json.dumps({key: snapshot[key] for key in ('drafts', 'batches', 'media_assets')}, sort_keys=True))
                    self.assertTrue(all(path == '/api/publisher/queue/presentation' for method, path, _ in QueueFixtureHandler.requests if method == 'POST'))
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_reservation_deep_link_opens_exact_active_history_or_hidden_without_restore(self):
        from playwright.sync_api import sync_playwright

        snapshot = QueueFixtureHandler.snapshot
        for index, draft in enumerate(snapshot['drafts'], 1):
            draft['media_asset_id'] = 500 + index
        snapshot['drafts'][2]['status'] = 'PUBLISHED'
        history_item = {**snapshot['drafts'][2], 'id': None, 'draft_id': 3, 'draft_batch_id': 24,
                        'item_status': 'SUCCEEDED', 'draft_status': 'PUBLISHED'}
        snapshot['history'] = {'executions': [{'id': execution_id, 'batch_id': 24, 'items': [history_item], 'total': 1}
                                               for execution_id in (61, 60)],
                               'total_count': 2, 'has_more': False, 'next_before_id': None}
        snapshot['queue_presentation'] = {'targets': [], 'history': []}
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    page.locator('[data-queue-draft="1"]').wait_for()
                    scenarios = [(1, [], 'ACTIVE'), (3, [], 'HISTORY'),
                        (1, [{'target_type': 'DRAFT', 'target_id': 1}], 'HIDDEN'),
                        (1, [{'target_type': 'BATCH', 'target_id': 24}], 'HIDDEN'),
                        (3, [{'target_type': 'EXECUTION', 'target_id': 61}], 'HIDDEN')]
                    for draft_id, targets, expected_view in scenarios:
                        with self.subTest(view=expected_view, targets=targets):
                            for target in targets:
                                target['hidden_at'] = '2026-10-05T10:00:00Z'
                            snapshot['queue_presentation']['targets'] = targets
                            page.evaluate("""({targets,draftId})=>{
                              state.queue_presentation={targets,history:[]};render();view('library');
                              document.querySelector('#fixtureReservation')?.remove();
                              const button=document.createElement('button');button.id='fixtureReservation';
                              button.dataset.reservationDraftId=String(draftId);button.dataset.reservationMediaId=String(500+draftId);
                              button.dataset.reservationBatchId='24';button.textContent='Em uso';document.querySelector('#library').prepend(button);
                            }""", {'targets': targets, 'draftId': draft_id})
                            page.locator('#fixtureReservation').click()
                            page.wait_for_function("expected=>document.querySelector(`[data-queue-view=\"${expected}\"]`)?.getAttribute('aria-selected')==='true'", arg=expected_view)
                            selector = f'[data-queue-draft="{draft_id}"]' if expected_view == 'ACTIVE' else f'[data-history-item-key="61:{draft_id}"]' if expected_view == 'HISTORY' else f'[data-hidden-draft="{draft_id}"]'
                            row = page.locator(selector).first
                            self.assertTrue(row.is_visible())
                            self.assertIn('queue-target', row.get_attribute('class'))
                            self.assertTrue(row.evaluate('(node)=>node===document.activeElement'))
                            self.assertEqual(page.evaluate('state.queue_presentation.targets'), targets)
                    self.assertFalse([request for request in QueueFixtureHandler.requests if request[0] == 'POST'])
                    self.assertFalse(errors, errors)
                finally:
                    context.close()
        finally:
            server.shutdown()
            server.server_close()

    def test_refined_cards_action_hierarchy_and_temporary_navigation_highlight(self):
        from playwright.sync_api import sync_playwright

        snapshot = QueueFixtureHandler.snapshot
        long_name = 'Video longo ' * 40 + '.mp4'
        first = snapshot['drafts'][0]
        first.update(filename=long_name, remote_file_id='safe_file', media_asset_id=501,
                     batch_name='Perfumes', folder_name='Body Splash', persistence_status='PERSISTED',
                     media_status='CONSUMED_PENDING_DELETE', queue_display={
                         'label': 'Envio a verificar', 'kind': 'attention',
                         'message': 'Não envie novamente.', 'persistence_label': 'Salvo no TikTok'})
        first['queue_capabilities'] = {**first['queue_capabilities'], 'can_verify': True}
        snapshot['drafts'][1].update(product_id=None, product_name=None, product_short_name=None)
        server = ThreadingHTTPServer(('127.0.0.1', 0), QueueFixtureHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
        try:
            with tempfile.TemporaryDirectory() as profile, sync_playwright() as playwright:
                context = playwright.chromium.launch_persistent_context(profile, channel='chrome', headless=True)
                try:
                    page = context.pages[0]
                    errors = []
                    page.on('pageerror', lambda error: errors.append(str(error)))
                    page.route('**/api/publisher/library/thumbnail/**', lambda route: route.fulfill(status=404))
                    page.goto(f'http://127.0.0.1:{server.server_port}/publisher.html')
                    row = page.locator('[data-queue-draft="1"]')
                    row.wait_for()
                    self.assertEqual(row.locator('.queue-filename').get_attribute('title'), long_name)
                    self.assertEqual(row.locator('.queue-product-name').inner_text(), 'Body Splash')
                    self.assertEqual(row.locator('.queue-folder').inner_text(), 'Body Splash')
                    self.assertEqual(row.locator('.queue-product > strong').inner_text(), 'Perfumes')
                    self.assertEqual(row.locator('.queue-status > .queue-state').count(), 1)
                    self.assertTrue(row.locator('[data-verify-draft].primary').is_visible())
                    self.assertEqual(row.locator('.queue-danger-actions .primary').count(), 0)
                    self.assertEqual(row.locator('.queue-presentation-actions [data-queue-presentation]').count(), 1)
                    self.assertEqual(row.locator('.queue-danger-actions [data-remove-draft]').get_attribute('data-remove-draft'), '1')
                    self.assertEqual(row.locator('.queue-danger-actions [data-cleanup]').get_attribute('data-cleanup'), '1')
                    self.assertIn('Produto não atribuído', page.locator('[data-queue-draft="2"]').inner_text())
                    self.assertIn('Lote não informado', page.locator('[data-queue-draft="3"]').inner_text())
                    page.locator('#queueSearch').fill('Video longo')
                    page.locator('#filter-product').select_option('1')
                    page.locator('#filter-status').select_option('READY')
                    page.evaluate("""() => {
                      window.queueScrollCalls=0;Element.prototype.scrollIntoView=function(){window.queueScrollCalls++;};
                      view('library');const button=document.createElement('button');button.id='cardNavigation';
                      button.dataset.reservationDraftId='1';button.dataset.reservationMediaId='501';button.dataset.reservationBatchId='24';
                      button.textContent='Em uso';document.querySelector('#library').prepend(button);
                    }""")
                    page.locator('#cardNavigation').click()
                    page.wait_for_function("document.querySelector('[data-queue-draft=\"1\"]')?.classList.contains('queue-target')")
                    self.assertEqual(page.locator('#queueSearch').input_value(), 'Video longo')
                    self.assertEqual(page.locator('#filter-product').input_value(), '1')
                    self.assertEqual(page.locator('#filter-status').input_value(), '')
                    self.assertTrue(row.locator('[data-queue-details]').evaluate('(node)=>node.open'))
                    page.wait_for_timeout(11200)
                    self.assertEqual(page.evaluate('window.queueScrollCalls'), 1)
                    self.assertFalse(row.evaluate('(node)=>node.classList.contains("queue-target")'))
                    self.assertTrue(row.locator('[data-queue-details]').evaluate('(node)=>node.open'))
                    self.assertEqual(page.locator('#queueSearch').input_value(), 'Video longo')
                    page.set_viewport_size({'width': 390, 'height': 844})
                    self.assertLessEqual(page.evaluate('document.documentElement.scrollWidth'), 410)
                    self.assertFalse([request for request in QueueFixtureHandler.requests if request[0] == 'POST'])
                    self.assertFalse(errors, errors)
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
                    page.locator('[data-queue-draft="2"] > details > summary').click()
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
                    self.assertIn('Verificar no TikTok', page.locator('[data-queue-draft="1"]').inner_text())
                    page.locator('[data-queue-draft="1"] [data-verify-draft]').click()
                    page.wait_for_function("document.querySelector('#notice')?.textContent.includes('Verificação registrada')")
                    self.assertIn(('POST', '/api/publisher/verify-reconciliation', {'id': 1}),
                                  QueueFixtureHandler.requests)
                    self.assertFalse(any(path.endswith('/resume-batch') for method, path, _ in QueueFixtureHandler.requests
                                         if method == 'POST'))
                    for outcome, expected in (
                        ('REMOTE_EXISTS_CONFIRMED', 'Rascunho correspondente encontrado'),
                        ('REMOTE_MATCH_AMBIGUOUS', 'Mais de um possível rascunho'),
                        ('REMOTE_STATE_UNKNOWN', 'Não foi possível concluir a verificação'),
                    ):
                        page.evaluate("""outcome => {
                          state.drafts[0].reconciliation_evidence={outcome,current:true,blocker_code:'LISTING_ERROR'};
                          renderQueue();
                        }""", outcome)
                        self.assertIn(expected, page.locator('[data-queue-draft="1"]').inner_text())
                    page.evaluate("""() => {
                      const draft=state.drafts[0];
                      draft.status='MANUAL_ACTION_REQUIRED';draft.media_status='NEEDS_RECONCILIATION';
                      draft.queue_capabilities={can_edit:false,can_remove:false,can_reorder:false,can_verify:true,remote_effect_possible:true};
                      draft.queue_display={label:'Requer verificação',kind:'attention',message:'Confirme o resultado antes de qualquer nova ação.'};
                      renderQueue();
                    }""")
                    self.assertIn('Verificar no TikTok', page.locator('[data-queue-draft="1"]').inner_text())
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
                    page.locator('[data-queue-view="HISTORY"]').click()
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-item-key="501:1"]').count(), 1)
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-item-key="500:1"]').count(), 1)
                    self.assertEqual(page.locator('#queueHistoryEntries button:not([data-queue-presentation])').count(), 0)
                    page.locator('#queueHistoryEntries [data-history-execution="501"] > summary').click()

                    page.locator('[data-queue-view="ACTIVE"]').click()
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
                    page.locator('[data-queue-draft="1"] > details > summary').click()
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
                    page.locator('[data-queue-view="HISTORY"]').click()
                    page.locator('#queueHistoryLabel').wait_for(timeout=10000)
                    self.assertTrue(page.locator('#queueHistory').evaluate('(node) => node.open'))
                    self.assertEqual(page.locator('#queueHistoryEntries [data-history-execution]').count(), 30)
                    self.assertTrue(page.locator('#queueHistory').evaluate('(node) => node.open'))
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
