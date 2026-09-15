"""Read-only browser diagnostics; no navigation, HTTP calls, or account writes."""
import json
import re
import threading
from collections import defaultdict, deque
from time import time
from pathlib import Path
from urllib.parse import urlsplit, unquote
from weakref import WeakKeyDictionary, WeakSet

from src.store import now_iso
from src.publisher.auth import is_login_url, is_studio_url, evaluate_studio_snapshot
from src.publisher.qr_response_state import QR_ENDPOINTS


PATH_WORDS = set('login qrcode phone-or-email email tiktokstudio upload content analytics '
                 'passport web api v1 v2 auth oauth authorize callback session account '
                 'check_qrconnect get_qrcode scan_qrcode qrconnect logout token register '
                 'verify captcha challenge static assets js css'.split())
ERROR_TYPES = {'Error', 'TimeoutError', 'TypeError', 'ValueError', 'RuntimeError',
               'SecurityError', 'NotSupportedError', 'InvalidStateError', 'TargetClosedError',
               'ReferenceError', 'SyntaxError', 'AbortError', 'DataError', 'QuotaExceededError'}


def diagnostic_json(value):
    """Return console-safe structured output; classification never uses this path."""
    return json.dumps(value, ensure_ascii=True, separators=(',', ':'), default=str)


def write_diagnostic_snapshot(path, snapshot):
    """Keep human-readable Unicode evidence in an explicit UTF-8 file."""
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(snapshot, ensure_ascii=False, indent=2, default=str), encoding='utf-8')
    return target


def safe_url(value):
    try:
        parsed = urlsplit(str(value))
        if parsed.scheme not in {'http', 'https'}:
            return 'about:blank' if str(value) == 'about:blank' else '[redacted-url]'
        host = parsed.hostname or ''
        path = '/'.join(part if part in PATH_WORDS else '[redacted]' for part in
                        unquote(parsed.path).split('/') if part)
        return f'{parsed.scheme}://{host}/' + path
    except Exception:
        return '[invalid-url]'


def safe_name(value):
    text = str(value or '')
    text = re.sub(r'https?://\S+', lambda m: safe_url(m[0]), text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+\S+', '[redacted]', text)
    text = re.sub(r'(?i)\b(?:authorization|cookie|password|passwd|credential|token|secret|code|sessionid)\s*[:=].*', '[redacted]', text)
    text = re.sub(r'(?i)\b(?:password|credential|token|secret)\s+.*', '[redacted]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[redacted-email]', text)
    text = re.sub(r'[A-Za-z0-9_+/=.-]{24,}', '[redacted-id]', text)
    return text[:180]


def safe_message(value):
    """Keep useful error prose, redact secret values independently."""
    text = str(value or '')[:16000]
    text = re.sub(r'https?://[^\s<>"\']+', lambda m: safe_url(m[0]), text)
    text = re.sub(r'(?im)\b(?:set-cookie|cookie)\s*:\s*[^\r\n]*', 'cookie: [redacted]', text)
    text = re.sub(r'(?i)\b(?:bearer|basic)\s+[A-Za-z0-9._~+/=-]+', '[redacted-authorization]', text)
    text = re.sub(r'''(?ix)(["']?\b(?:authorization|password|passwd|access_token|refresh_token|token|ticket|api[_-]?key|session[_-]?(?:id|token)|sid_tt|msToken|credential|secret|qr[_-]?(?:content|code))["']?\s*[:=]\s*)(?:"[^"\r\n]*"|'[^'\r\n]*'|[^\s,;}]+)''', r'\1[redacted]', text)
    text = re.sub(r'\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b', '[redacted-jwt]', text)
    text = re.sub(r'\b(?:sk|pk|api)[_-][A-Za-z0-9_-]{8,}\b', '[redacted-key]', text)
    text = re.sub(r'[A-Za-z0-9_+/=-]{24,}', '[redacted-blob]', text)
    text = re.sub(r'[\w.+-]+@[\w.-]+\.[a-zA-Z]{2,}', '[redacted-email]', text)
    return text[:2000]


def console_metadata(message):
    location = getattr(message, 'location', {}) or {}
    def coordinate(name):
        value = location.get(name)
        return value if isinstance(value, int) and not isinstance(value, bool) and value >= 0 else None
    return {'level': message.type if message.type in {'error','warning','log','info','debug'} else 'other',
            'message': safe_message(getattr(message, 'text', '')),
            'source_url': safe_url(location.get('url', '')),
            'line': coordinate('lineNumber'), 'column': coordinate('columnNumber')}


def safe_error(operation, exc, page_id=None):
    name = exc if isinstance(exc, str) else type(exc).__name__
    return {'operation': operation, 'error_type': name if name in ERROR_TYPES else 'Error',
            'page_id': page_id, 'message': 'Coleta falhou; mensagem original omitida para proteger segredos'}


def cookie_metadata(cookie):
    return {'name': safe_name(cookie.get('name')), 'domain': safe_name(cookie.get('domain')),
            'path': urlsplit(safe_url('https://tiktok.com' + cookie.get('path','/'))).path,
            'expires': cookie.get('expires') if isinstance(cookie.get('expires'), (int,float)) else None,
            'secure': cookie.get('secure') is True, 'httpOnly': cookie.get('httpOnly') is True,
            'sameSite': cookie.get('sameSite') if cookie.get('sameSite') in {'Strict','Lax','None'} else None}


DOM_SNAPSHOT = r"""async () => {
  const errors = [];
  const normalize = text => (text || '').normalize('NFD')
    .replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const patterns = {
    RATE_LIMITED: /too many attempts|too many requests|muitas tentativas|limite de tentativas/,
    LOGIN_REJECTED: /login rejected|login denied|login recusado|autenticacao recusada/,
    QR_EXPIRED: /qr.{0,25}(expired|expirad)|expired.{0,25}qr|codigo expirad/,
    CHALLENGE: /verify you are human|verifique se voce e humano|captcha|verification code|codigo de verificacao/,
    ERROR: /too many attempts|muitas tentativas|something went wrong|algo deu errado|couldn.t log in|nao foi possivel entrar/,
    CONFIRMATION_PENDING: /confirm.{0,40}(phone|app|celular|aplicativo)|aguardando confirmacao|waiting for confirmation/,
    QR_SCANNED: /qr.{0,25}(scanned|escanead)|code scanned|codigo escaneado/,
    CONFIRMATION_COMPLETE: /login successful|login realizado|successfully logged in|voce esta conectado|already logged in|nao e preciso repetir/,
    QR_WAITING_SCAN: /scan.{0,35}qr|escane.{0,35}qr|leia.{0,35}qr/
  };
  const match = text => Object.entries(patterns).filter(([,pattern]) => pattern.test(normalize(text))).map(([key]) => key);
  const body = document.body;
  const controls = body ? [...body.querySelectorAll('a,button,[role=button],[role=menuitem]')].filter(visible) : [];
  const label = el => normalize(el.getAttribute('aria-label') || el.innerText).trim();
  const signals = {
    visible: match(body ? body.innerText.slice(0,32000) : ''),
    hidden: [...document.querySelectorAll('[role=status],[role=alert],[aria-live],[hidden]')]
      .slice(0,100).filter(el => !visible(el)).flatMap(el => match((el.textContent || '').slice(0,2000))),
    logout: controls.some(el => /^(log out|logout|sign out|sair|sair da conta)$/.test(label(el))),
    login: controls.some(el => /^(log in|login|sign in|entrar|entrar no tiktok)$/.test(label(el)))
  };
  const read = (name, fn) => {try {return fn();} catch(e) {errors.push({operation:name,error_type:e.name});return null;}};
  const bounded = async (name, fn) => {
    let timer;
    try {return await Promise.race([fn(), new Promise((_,reject) => {
      timer=setTimeout(() => {const e=new Error();e.name='TimeoutError';reject(e);},1500);
    })]);} catch(e) {errors.push({operation:name,error_type:e.name});return null;}
    finally {clearTimeout(timer);}
  };
  const localKeys = read('localStorage', () => Object.keys(localStorage));
  const sessionKeys = read('sessionStorage', () => Object.keys(sessionStorage));
  const databases = await bounded('indexedDB', async () => {
    if (!globalThis.indexedDB?.databases) {const e=new Error();e.name='NotSupportedError';throw e;}
    return (await indexedDB.databases()).map(d => ({name:d.name,version:d.version}));
  });
  const workers = await bounded('serviceWorkers', async () => {
    if (!navigator.serviceWorker?.getRegistrations) {const e=new Error();e.name='NotSupportedError';throw e;}
    return (await navigator.serviceWorker.getRegistrations()).map(r => ({scope:r.scope,
      scriptURL:(r.active || r.waiting || r.installing)?.scriptURL || '',
      state:(r.active || r.waiting || r.installing)?.state || 'unknown'}));
  });
  return {url:location.href,title:document.title,readyState:document.readyState,
    visibilityState:document.visibilityState,signals,localStorageKeys:localKeys,
    sessionStorageKeys:sessionKeys,indexedDB:databases,serviceWorkers:workers,errors};
}"""

SEMANTIC_STATES = ('RATE_LIMITED', 'LOGIN_REJECTED', 'QR_EXPIRED', 'CHALLENGE', 'ERROR', 'CONFIRMATION_PENDING',
                   'QR_SCANNED', 'CONFIRMATION_COMPLETE', 'QR_WAITING_SCAN')


STUDIO_DOM_INVENTORY = r"""() => {
  const visible = el => !!el.getClientRects().length && getComputedStyle(el).visibility !== 'hidden';
  const nodes = [...document.querySelectorAll('nav,aside,header,main,a,button,img,[role],[aria-label]')]
    .filter(visible).slice(0,200);
  return nodes.map(el => ({tag:el.tagName.toLowerCase(),role:el.getAttribute('role'),
    ariaLabel:el.getAttribute('aria-label'),
    text:['A','BUTTON'].includes(el.tagName) ? (el.innerText || '').slice(0,160) : null,
    alt:el.tagName === 'IMG' ? el.getAttribute('alt') : null,
    href:el.tagName === 'A' ? el.getAttribute('href') : null,
    testId:el.getAttribute('data-testid') || el.getAttribute('data-e2e')}));
}"""


def sanitized_dom_inventory(raw):
    if not isinstance(raw, list):
        raise ValueError('Invalid DOM inventory')
    result = []
    for item in raw[:200]:
        href = item.get('href')
        if isinstance(href, str) and href.startswith('/'):
            href = 'https://www.tiktok.com' + href
        result.append({'tag':safe_name(item.get('tag')), 'role':safe_name(item.get('role')),
            'aria_label':safe_message(item.get('ariaLabel')), 'text':safe_message(item.get('text')),
            'alt':safe_message(item.get('alt')), 'href':safe_url(href) if href else None,
            'test_id':safe_name(item.get('testId'))})
    return result


def classify(snapshot):
    if evaluate_studio_snapshot(snapshot)['auth_result'] == 'AUTHENTICATED':
        return 'AUTHENTICATED'
    auth_page = next((p for p in snapshot['pages'] if p['is_auth_page']), None)
    if snapshot.get('detector_error') or snapshot['cookies'] is None or snapshot['session_cookie_present'] is None or not auth_page or not auth_page.get('dom_collected'):
        return 'UNKNOWN'
    state = auth_page['login_state']
    if state in {'UNKNOWN', 'CHALLENGE', 'ERROR'}:
        return 'UNKNOWN'
    present = snapshot['session_cookie_present']
    if present and state == 'AUTHENTICATED_PAGE':
        return 'AUTHENTICATED'
    if present and auth_page['is_login_page']:
        return 'AUTH_STATE_PRESENT_BUT_PAGE_STUCK'
    if not present and auth_page['is_login_page']:
        return 'AUTH_NOT_RECEIVED'
    return 'UNKNOWN'


class AuthDiagnostics:
    def __init__(self):
        self.lock = threading.RLock()
        self.events = defaultdict(lambda: deque(maxlen=200))
        self.detector_errors = {}
        self.page_ids = WeakKeyDictionary()
        self.page_urls = WeakKeyDictionary()
        self.attached_pages, self.attached_contexts = WeakSet(), WeakSet()
        self.next_id = 0
        self.event_sinks = {}

    def page_id(self, page):
        with self.lock:
            if page not in self.page_ids:
                self.next_id += 1
                self.page_ids[page] = f'page-{self.next_id}'
            return self.page_ids[page]

    def record(self, account_id, event, **metadata):
        with self.lock:
            item = {'timestamp': now_iso(), 'event': event, **metadata}
            self.events[account_id].append(item)
            sink = self.event_sinks.get(account_id)
            if sink:
                sink(item)

    def detector_failed(self, account_id, exc):
        error = safe_error('auth_detector', exc)
        with self.lock:
            self.detector_errors[account_id] = error
        self.record(account_id, 'DETECTOR_ERROR', **error)

    def detector_succeeded(self, account_id):
        with self.lock:
            self.detector_errors.pop(account_id, None)

    def recent(self, account_id):
        with self.lock:
            return list(self.events[account_id]), self.detector_errors.get(account_id)

    def listen(self, target, name, account_id, callback):
        def guarded(value=None):
            try:
                callback(value)
            except Exception as exc:
                self.record(account_id, 'LISTENER_ERROR', **safe_error(name, exc))
        target.on(name, guarded)

    def attach_context(self, account_id, context):
        if context in self.attached_contexts:
            return
        self.attached_contexts.add(context)
        self.listen(context, 'page', account_id, lambda page: self.attach_page(account_id, page))
        self.listen(context, 'close', account_id, lambda _: self.record(account_id, 'CONTEXT_CLOSED', origin='UNKNOWN'))
        for page in context.pages:
            self.attach_page(account_id, page)

    def attach_page(self, account_id, page):
        if page in self.attached_pages:
            return
        self.attached_pages.add(page)
        page_id = self.page_id(page)
        def event(kind, **values): self.record(account_id, kind, page_id=page_id, **values)
        self.listen(page, 'console', account_id, lambda message: event('CONSOLE', **console_metadata(message)))
        self.listen(page, 'pageerror', account_id, lambda exc: event('PAGE_ERROR',
                    error_type=type(exc).__name__ if type(exc).__name__ in ERROR_TYPES else 'Error',
                    message=safe_message(str(exc))))
        def request_failed(request):
            code = str(request.failure or '')
            allowed = {'net::ERR_ABORTED','net::ERR_FAILED','net::ERR_BLOCKED_BY_CLIENT',
                       'net::ERR_CONNECTION_RESET','net::ERR_TIMED_OUT','net::ERR_NAME_NOT_RESOLVED'}
            event('REQUEST_FAILED', **self.http_metadata(request), failure=code if code in allowed else 'REDACTED')
        self.listen(page, 'requestfailed', account_id, request_failed)
        self.listen(page, 'request', account_id, lambda request: event('HTTP_REQUEST', **self.http_metadata(request))
                    if account_id in self.event_sinks else None)
        def response_received(response):
            status = response.status
            endpoint = urlsplit(response.url if isinstance(getattr(response, 'url', None), str) else response.request.url)
            if (endpoint.hostname, endpoint.path.rstrip('/')) in QR_ENDPOINTS:
                event('QR_RESPONSE_STATE', **self.http_metadata(response.request), status=status,
                      state='UNKNOWN', reason='NO_VERIFIED_SCHEMA', body_read=False)
            if account_id in self.event_sinks or 300 <= status <= 399 or status in {401,403,408,409,429} or 500 <= status <= 599:
                event('HTTP_RESPONSE', **self.http_metadata(response.request), status=status)
        self.listen(page, 'response', account_id, response_received)
        def navigated(frame):
            event('FRAME_NAVIGATED', url=safe_url(frame.url), main_frame=frame is page.main_frame)
            if frame is page.main_frame:
                self.observe_url(account_id, page, frame.url)
        self.listen(page, 'framenavigated', account_id, navigated)
        def popup(child):
            self.attach_page(account_id, child)
            event('POPUP', child_page_id=self.page_id(child), url=safe_url(child.url))
        self.listen(page, 'popup', account_id, popup)
        self.listen(page, 'close', account_id, lambda _: event('PAGE_CLOSED', origin='UNKNOWN'))

    def observe_url(self, account_id, page, url):
        sanitized = safe_url(url)
        previous = self.page_urls.get(page)
        if previous != sanitized:
            self.page_urls[page] = sanitized
            self.record(account_id, 'URL_CHANGED', page_id=self.page_id(page), url=sanitized)

    @staticmethod
    def http_metadata(request):
        parsed = urlsplit(safe_url(request.url))
        kind = request.resource_type
        method = getattr(request, 'method', 'UNKNOWN')
        previous = getattr(request, 'redirected_from', None)
        following = getattr(request, 'redirected_to', None)
        return {'host': parsed.hostname, 'path': parsed.path,
                'method': method if method in {'GET','POST','PUT','PATCH','DELETE','HEAD','OPTIONS'} else 'UNKNOWN',
                'redirected_from': safe_url(previous.url) if previous else None,
                'redirected_to': safe_url(following.url) if following else None,
                'resource_type': kind if kind in
                {'document','stylesheet','image','media','font','script','texttrack','xhr','fetch','eventsource','websocket','manifest','other'} else 'other'}

    def empty(self, account_id, status='NO_ACTIVE_CONTEXT', errors=None):
        events, detector_error = self.recent(account_id)
        return {'schema_version': 1, 'account_id': account_id, 'captured_at': now_iso(),
                'status': status, 'diagnostic_state': 'UNKNOWN', 'context_count': 0,
                'page_count': 0, 'auth_page_id': None, 'pages': [], 'cookies': None,
                'session_cookie_present': None, 'errors': errors or [],
                'detector_error': detector_error, 'events': events}

    def collect(self, account_id, context, auth_page):
        snapshot = self.empty(account_id, 'OK')
        if context is None:
            snapshot['status'] = 'NO_ACTIVE_CONTEXT'
            return snapshot
        snapshot['context_count'] = 1
        if auth_page is not None:
            snapshot['auth_page_id'] = self.page_id(auth_page)
        def failure(operation, exc, page_id=None):
            error = safe_error(operation, exc, page_id)
            snapshot['errors'].append(error)
            self.record(account_id, 'COLLECTION_ERROR', **error)
        try:
            pages = list(context.pages)
            snapshot['page_count'] = len(pages)
        except Exception as exc:
            pages = []
            failure('pages', exc)
        try:
            cookies = [c for c in context.cookies() if str(c.get('domain','')).lstrip('.') == 'tiktok.com'
                       or str(c.get('domain','')).lstrip('.').endswith('.tiktok.com')]
            snapshot['cookies'] = [cookie_metadata(c) for c in cookies]
            snapshot['session_cookie_present'] = any(c.get('name') in {'sessionid','sessionid_ss','sid_tt'}
                and bool(c.get('value')) and (c.get('expires',-1) == -1 or c.get('expires',0) > time()) for c in cookies)
        except Exception as exc:
            failure('cookies', exc)
        for page in pages:
            page_id = self.page_id(page)
            result = {'page_id': page_id, 'is_auth_page': page is auth_page, 'url': None,
                      'title': None, 'readyState': None, 'visibilityState': None,
                      'login_state': 'UNKNOWN', 'dom_collected': False, 'is_login_page': False}
            snapshot['pages'].append(result)
            try:
                result['url'] = safe_url(page.url)
                raw = page.evaluate(DOM_SNAPSHOT)
                self.observe_url(account_id, page, raw['url'])
                result.update(url=safe_url(raw['url']), title=safe_name(raw.get('title')),
                    readyState=raw.get('readyState') if raw.get('readyState') in {'loading','interactive','complete'} else 'unknown',
                    visibilityState=raw.get('visibilityState') if raw.get('visibilityState') in {'visible','hidden','prerender'} else 'unknown',
                    is_login_page=is_login_url(raw['url']), dom_collected=True)
                if is_studio_url(raw['url']):
                    try:
                        result['studio_dom_inventory'] = sanitized_dom_inventory(page.evaluate(STUDIO_DOM_INVENTORY))
                    except Exception as exc:
                        failure('studio_dom_inventory', exc, page_id)
                    try:
                        result['accessibility_snapshot'] = safe_message(page.locator('body').aria_snapshot(timeout=2000))
                    except Exception as exc:
                        failure('accessibility_snapshot', exc, page_id)
                signals = raw.get('signals', {})
                result['login_control_visible'] = signals.get('login') is True
                result['logout_control_visible'] = signals.get('logout') is True
                visible = [s for s in SEMANTIC_STATES if s in signals.get('visible', [])]
                hidden = [s for s in SEMANTIC_STATES if s in signals.get('hidden', [])]
                result['semantic_signals'] = {'visible': visible, 'hidden': hidden}
                result['login_state'] = next(iter(visible), 'UNKNOWN')
                blocked = set(visible) - {'CONFIRMATION_COMPLETE'}
                if is_studio_url(raw['url']) and signals.get('logout') is True and not signals.get('login') and not blocked:
                    result['login_state'] = 'AUTHENTICATED_PAGE'
                for name in ('localStorageKeys','sessionStorageKeys'):
                    result[name] = [safe_name(k) for k in raw[name]] if isinstance(raw.get(name), list) else None
                result['indexedDB'] = [{'name':safe_name(d.get('name')), 'version':d.get('version') if isinstance(d.get('version'),int) else None}
                                      for d in raw['indexedDB']] if isinstance(raw.get('indexedDB'),list) else None
                result['serviceWorkers'] = [{'scope':safe_url(w.get('scope')), 'scriptURL':safe_url(w.get('scriptURL')),
                    'state':w.get('state') if w.get('state') in {'installing','installed','activating','activated','redundant'} else 'unknown'}
                    for w in raw['serviceWorkers']] if isinstance(raw.get('serviceWorkers'),list) else None
                for error in raw.get('errors', []):
                    operation = error.get('operation')
                    failure(operation if operation in {'localStorage','sessionStorage','indexedDB','serviceWorkers'} else 'document', error.get('error_type','Error'), page_id)
            except Exception as exc:
                result['dom_collected'] = False
                failure('document', exc, page_id)
        snapshot['status'] = 'PARTIAL' if snapshot['errors'] else 'OK'
        snapshot['diagnostic_state'] = classify(snapshot)
        snapshot['events'], snapshot['detector_error'] = self.recent(account_id)
        return snapshot
