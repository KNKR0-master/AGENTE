"""Mocks only: no Chrome, no TikTok, no writes to production data."""
import copy
import ast
import io
import json
import tempfile
import threading
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from src.publisher.auth import LoginSession
from src.publisher.diagnostics import AuthDiagnostics, safe_url, safe_name
from src.publisher.worker import PublisherWorker
from src.publisher.http import PublisherAPI
from src.publisher.service import PublisherService, init_publisher
from src.store import connect, init_db


class Emitter:
    def __init__(self): self.listeners = {}
    def on(self, name, callback): self.listeners.setdefault(name, []).append(callback)
    def emit(self, name, value=None):
        for callback in self.listeners.get(name, []): callback(value)


class Page(Emitter):
    def __init__(self, url='https://www.tiktok.com/login/qrcode?token=DO_NOT_LEAK'):
        super().__init__()
        self.url = url
        self.main_frame = SimpleNamespace(url=url)
        self.closed = False
        self.raw = {'url':url,'title':'Log in | TikTok','readyState':'complete',
            'visibilityState':'visible','signals':{'visible':['QR_WAITING_SCAN'],'hidden':[]},
            'localStorageKeys':['language','access_token'],'sessionStorageKeys':['login_state'],
            'indexedDB':[{'name':'local-store','version':1}],
            'serviceWorkers':[{'scope':'https://www.tiktok.com/','scriptURL':'https://www.tiktok.com/sw.js?token=DO_NOT_LEAK','state':'activated'}],
            'errors':[]}
        self.evaluate = MagicMock(side_effect=lambda script: copy.deepcopy(self.raw))
        self.goto = MagicMock(side_effect=AssertionError('NAVIGATION FORBIDDEN'))
        self.reload = MagicMock(side_effect=AssertionError('RELOAD FORBIDDEN'))
    def is_closed(self): return self.closed


class Context(Emitter):
    def __init__(self, pages):
        super().__init__()
        self.pages = pages
        self.cookies = MagicMock(return_value=[])
        self.new_page = MagicMock(side_effect=AssertionError('NEW PAGE FORBIDDEN'))


COOKIE = {'name':'sessionid','domain':'.tiktok.com','path':'/','expires':-1,
          'secure':True,'httpOnly':True,'sameSite':'None','value':'DO_NOT_LEAK'}


class SnapshotTests(unittest.TestCase):
    def setUp(self):
        self.network = patch('socket.socket.connect', side_effect=AssertionError('NETWORK FORBIDDEN'))
        self.network.start()
        self.addCleanup(self.network.stop)
        self.diag = AuthDiagnostics()
        self.page = Page()
        self.context = Context([self.page])

    def collect(self): return self.diag.collect(1,self.context,self.page)

    def test_one_page_with_metadata_and_read_only_calls(self):
        result = self.collect()
        self.assertEqual(result['status'],'OK')
        self.assertEqual(result['diagnostic_state'],'AUTH_NOT_RECEIVED')
        self.assertEqual(result['page_count'],1)
        self.assertEqual(result['context_count'],1)
        page = result['pages'][0]
        self.assertEqual(result['auth_page_id'],page['page_id'])
        self.assertEqual(page['title'],'Log in | TikTok')
        self.assertEqual(page['readyState'],'complete')
        self.assertEqual(page['localStorageKeys'],['language','access_token'])
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))
        self.context.new_page.assert_not_called()
        self.page.goto.assert_not_called()
        self.page.reload.assert_not_called()

    def test_multiple_pages_identify_auth_page_without_replacing_it(self):
        other = Page('https://www.tiktok.com/tiktokstudio')
        self.context.pages.append(other)
        result = self.collect()
        self.assertEqual(result['page_count'],2)
        self.assertEqual([p['is_auth_page'] for p in result['pages']],[True,False])
        self.assertNotEqual(result['pages'][0]['page_id'],result['pages'][1]['page_id'])
        self.assertEqual(self.collect()['auth_page_id'],result['auth_page_id'])

    def test_no_context_is_explicit(self):
        result = self.diag.collect(1,None,None)
        self.assertEqual(result['status'],'NO_ACTIVE_CONTEXT')
        self.assertEqual(result['diagnostic_state'],'UNKNOWN')

    def test_dom_failure_is_explicit_unknown_and_sanitized(self):
        self.page.evaluate.side_effect = RuntimeError('Authorization: Bearer DO_NOT_LEAK')
        result = self.collect()
        self.assertEqual(result['diagnostic_state'],'UNKNOWN')
        self.assertEqual(result['status'],'PARTIAL')
        self.assertEqual(result['errors'][0]['operation'],'document')
        self.assertEqual(result['errors'][0]['error_type'],'RuntimeError')
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))

    def test_cookie_failure_is_not_missing_auth(self):
        self.context.cookies.side_effect = TimeoutError('cookie=DO_NOT_LEAK')
        result = self.collect()
        self.assertIsNone(result['cookies'])
        self.assertIsNone(result['session_cookie_present'])
        self.assertEqual(result['diagnostic_state'],'UNKNOWN')
        self.assertEqual(result['errors'][0]['operation'],'cookies')

    def test_cookies_project_exact_fields_and_qr_stuck_is_not_verified(self):
        self.context.cookies.return_value = [COOKIE,{**COOKIE,'domain':'.evil.test'}]
        result = self.collect()
        self.assertEqual(len(result['cookies']),1)
        self.assertEqual(set(result['cookies'][0]),{'name','domain','path','expires','secure','httpOnly','sameSite'})
        self.assertEqual(result['diagnostic_state'],'AUTH_STATE_PRESENT_BUT_PAGE_STUCK')
        self.assertNotIn('session_verified',result)
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))

    def test_authenticated_requires_protected_page_and_positive_marker(self):
        self.context.cookies.return_value = [COOKIE]
        self.page.raw.update(url='https://www.tiktok.com/tiktokstudio',signals={'visible':[],'hidden':[],'logout':True,'login':False})
        self.assertEqual(self.collect()['diagnostic_state'],'AUTHENTICATED')
        self.page.raw['signals']['logout'] = False
        self.assertEqual(self.collect()['diagnostic_state'],'UNKNOWN')

    def test_expired_cookie_and_unknown_dom_do_not_prove_authentication(self):
        self.context.cookies.return_value = [{**COOKIE,'expires':1}]
        self.page.raw['signals']['visible'] = []
        self.assertEqual(self.collect()['diagnostic_state'],'UNKNOWN')

    def test_semantic_states_and_hidden_messages(self):
        for state in ['QR_WAITING_SCAN','QR_SCANNED','CONFIRMATION_PENDING','QR_EXPIRED','CHALLENGE','ERROR','CONFIRMATION_COMPLETE']:
            self.page.raw['signals']={'visible':[state],'hidden':['ERROR']}
            result = self.collect()
            self.assertEqual(result['pages'][0]['login_state'],state)
            self.assertEqual(result['pages'][0]['semantic_signals']['hidden'],['ERROR'])
            if state in {'CHALLENGE','ERROR'}:
                self.assertEqual(result['diagnostic_state'],'UNKNOWN')

    def test_storage_enumeration_errors_remain_visible(self):
        self.page.raw.update(indexedDB=None,localStorageKeys=None,
            errors=[{'operation':'indexedDB','error_type':'NotSupportedError'},
                    {'operation':'localStorage','error_type':'SecurityError'}])
        result = self.collect()
        self.assertEqual(result['status'],'PARTIAL')
        self.assertEqual(len(result['errors']),2)
        self.assertIsNone(result['pages'][0]['indexedDB'])

    def test_detached_detector_override_stays_unknown_until_successful_observation(self):
        self.diag.detector_failed(1,RuntimeError('token=DO_NOT_LEAK'))
        self.assertEqual(self.collect()['diagnostic_state'],'UNKNOWN')
        self.diag.detector_succeeded(1)
        self.assertEqual(self.collect()['diagnostic_state'],'AUTH_NOT_RECEIVED')

    def test_sanitization_strips_credentials_query_fragment_and_dynamic_path(self):
        url='https://user:password@www.tiktok.com/passport/web/DO_NOT_LEAK?token=DO_NOT_LEAK#DO_NOT_LEAK'
        self.assertEqual(safe_url(url),'https://www.tiktok.com/passport/web/[redacted]')
        for text in ['Bearer DO_NOT_LEAK','token=DO_NOT_LEAK','password DO_NOT_LEAK','user@example.com']:
            self.assertNotIn('DO_NOT_LEAK',safe_name(text))
            self.assertNotIn('user@example.com',safe_name(text))

    def test_passive_listeners_attach_once_before_navigation_and_capture_metadata(self):
        self.diag.attach_context(1,self.context)
        self.diag.attach_context(1,self.context)
        self.assertEqual(len(self.page.listeners['console']),1)
        self.page.emit('console',SimpleNamespace(type='error',text='token=DO_NOT_LEAK'))
        self.page.emit('pageerror',RuntimeError('password=DO_NOT_LEAK'))
        request=SimpleNamespace(url=self.page.url,resource_type='xhr',failure='net::ERR_ABORTED')
        self.page.emit('requestfailed',request)
        for status in [200,302,401,403,408,409,429,500,503]:
            self.page.emit('response',SimpleNamespace(status=status,request=request))
        self.page.emit('framenavigated',self.page.main_frame)
        child=Page()
        self.context.emit('page',child)
        self.page.emit('popup',child)
        self.assertEqual(len(child.listeners['console']),1)
        self.page.emit('close')
        self.context.emit('close')
        events,_=self.diag.recent(1)
        self.assertEqual([e['status'] for e in events if e['event']=='HTTP_RESPONSE'],[302,401,403,408,409,429,500,503])
        kinds={e['event'] for e in events}
        self.assertTrue({'CONSOLE','PAGE_ERROR','REQUEST_FAILED','FRAME_NAVIGATED','URL_CHANGED','POPUP','PAGE_CLOSED','CONTEXT_CLOSED'} <= kinds)
        self.assertNotIn('DO_NOT_LEAK',json.dumps(events))
        self.context.new_page.assert_not_called()
        self.page.goto.assert_not_called()

    def test_listener_failure_and_event_buffer_are_bounded(self):
        self.diag.attach_context(1,self.context)
        self.page.emit('response',None)
        self.assertEqual(self.diag.recent(1)[0][-1]['event'],'LISTENER_ERROR')
        for _ in range(250): self.page.emit('console',SimpleNamespace(type='info'))
        self.assertEqual(len(self.diag.recent(1)[0]),200)


class WorkerDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root=Path(self.temp.name)
        self.db=self.root/'fixture.db'
        self.conn=connect(self.db)
        self.addCleanup(self.conn.close)
        init_db(self.conn)
        init_publisher(self.conn)
        PublisherService(self.conn,self.root).save_account({'name':'Fixture'})
        self.worker=PublisherWorker(self.root,self.db)
        self.page=Page()
        self.context=Context([self.page])
        self.start=patch.object(self.worker,'start',side_effect=AssertionError('START FORBIDDEN')).start()
        self.addCleanup(patch.stopall)
        patch('socket.socket.connect',side_effect=AssertionError('NETWORK FORBIDDEN')).start()

    def live_fixture(self):
        self.worker.account_lock(1)
        self.worker.contexts[1]=self.context
        self.worker.login_sessions[1]=LoginSession(page=self.page)
        self.worker.thread=SimpleNamespace(is_alive=lambda:True)

    def test_get_without_context_never_starts_worker(self):
        self.assertEqual(self.worker.get_auth_diagnostic_snapshot(1)['status'],'NO_ACTIVE_CONTEXT')
        self.start.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_get_marshals_reads_to_existing_owner_and_never_navigates(self):
        self.live_fixture()
        queued=threading.Event()
        original=self.worker.diagnostic_requests.put_nowait
        def put(item): original(item);queued.set()
        with patch.object(self.worker.diagnostic_requests,'put_nowait',side_effect=put), ThreadPoolExecutor(max_workers=1) as pool:
            response=pool.submit(self.worker.get_auth_diagnostic_snapshot,1)
            self.assertTrue(queued.wait(2))
            self.page.evaluate.assert_not_called()
            self.worker.process_diagnostic_requests()
            result=response.result(timeout=2)
        self.assertEqual(result['page_count'],1)
        self.start.assert_not_called()
        self.context.new_page.assert_not_called()
        self.page.goto.assert_not_called()
        self.assertTrue(self.worker.commands.empty())

    def test_busy_timeout_does_not_fall_back_to_start_or_context(self):
        self.live_fixture()
        result=self.worker.get_auth_diagnostic_snapshot(1,timeout=.001)
        self.assertEqual(result['status'],'SNAPSHOT_BUSY')
        self.worker.process_diagnostic_requests()
        self.page.evaluate.assert_not_called()

    def test_detector_error_is_logged_unknown_without_any_account_write(self):
        self.live_fixture()
        self.worker.auth_detector.observe=MagicMock(side_effect=RuntimeError('token=DO_NOT_LEAK'))
        with patch('src.publisher.worker.persist_auth',side_effect=AssertionError('WRITE FORBIDDEN')) as persist:
            self.worker.poll_login_sessions()
            persist.assert_not_called()
        result=self.worker.auth_diagnostics.collect(1,self.context,self.page)
        self.assertEqual(result['diagnostic_state'],'UNKNOWN')
        self.assertEqual(result['detector_error']['error_type'],'RuntimeError')
        self.assertNotIn('DO_NOT_LEAK',json.dumps(result))

    def test_context_delegates_to_cdp_runtime_without_persistent_launch(self):
        (self.root/'config').mkdir()
        (self.root/'config/publisher_studio.json').write_text('{"tiktok_actions_paused":false}')
        playwright=MagicMock()
        profile='data/publisher/browser_profiles/existing-fixture'
        profile_root=self.root/'browser-profiles'
        expected=profile_root/'existing-fixture'
        expected.mkdir(parents=True)
        runtime_get_context=MagicMock(return_value=self.context)
        self.worker.browser_runtime.get_context=runtime_get_context
        with patch.dict('os.environ',{'PUBLISHER_BROWSER_PROFILE_ROOT':str(profile_root)}):
            self.worker.context(playwright,{'id':1,'browser_profile':profile})
        runtime_get_context.assert_called_once_with(playwright,expected.resolve())
        playwright.chromium.launch_persistent_context.assert_not_called()
        self.assertIn('response',self.page.listeners)
        self.assertIn('console',self.page.listeners)
        self.page.goto.assert_not_called()


class EndpointTests(unittest.TestCase):
    def test_server_dispatch_does_not_enter_database_initialization(self):
        source=Path(__file__).resolve().parents[1]/'dashboard_server.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        handler=next(node for node in tree.body if isinstance(node,ast.ClassDef) and node.name=='Handler')
        route=next(node for node in handler.body if isinstance(node,ast.FunctionDef) and node.name=='_api_get')
        api=MagicMock()
        scope={'PUBLISHER':api,'connect':MagicMock(side_effect=AssertionError('DB INIT FORBIDDEN'))}
        exec(compile(ast.Module(body=[route],type_ignores=[]),str(source),'exec'),scope)
        fake=object()
        scope['_api_get'](fake,'/api/publisher/accounts/1/auth-diagnostics',{})
        api.get_auth_diagnostics.assert_called_once_with(fake,'/api/publisher/accounts/1/auth-diagnostics')
        scope['connect'].assert_not_called()
        writer=next(node for node in handler.body if isinstance(node,ast.FunctionDef) and node.name=='_json')
        scope={'json':json}
        exec(compile(ast.Module(body=[writer],type_ignores=[]),str(source),'exec'),scope)
        response=SimpleNamespace(send_response=MagicMock(),send_header=MagicMock(),end_headers=MagicMock(),wfile=io.BytesIO())
        scope['_json'](response,{'status':'UNKNOWN'},no_store=True)
        response.send_header.assert_any_call('Cache-Control','no-store')

    def test_get_reads_only_and_preserves_entire_account_row(self):
        with tempfile.TemporaryDirectory() as folder:
            root=Path(folder)
            db=root/'fixture.db'
            conn=connect(db)
            try:
                init_db(conn);init_publisher(conn)
                account=PublisherService(conn,root).save_account({'name':'Fixture'})
                before=tuple(conn.execute('SELECT * FROM publisher_accounts WHERE id=?',(account,)).fetchone())
                api=PublisherAPI(root,db)
                handler=SimpleNamespace(headers={'Host':'127.0.0.1:8765'},_json=MagicMock())
                with patch.object(api.worker,'start',side_effect=AssertionError('START FORBIDDEN')):
                    api.get_auth_diagnostics(handler,f'/api/publisher/accounts/{account}/auth-diagnostics')
                self.assertEqual(handler._json.call_args.args[0]['status'],'NO_ACTIVE_CONTEXT')
                self.assertTrue(handler._json.call_args.kwargs['no_store'])
                after=tuple(conn.execute('SELECT * FROM publisher_accounts WHERE id=?',(account,)).fetchone())
                self.assertEqual(before,after)
                self.assertTrue(api.worker.commands.empty())
                handler.headers['Origin']='https://foreign.example'
                with self.assertRaises(ValueError): api.get_auth_diagnostics(handler,f'/api/publisher/accounts/{account}/auth-diagnostics')
            finally: conn.close()


if __name__=='__main__': unittest.main()
