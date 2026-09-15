"""Passive Publisher authentication detection. Never navigates or requests an API."""
from dataclasses import dataclass
import re
import unicodedata
from time import time
from urllib.parse import urlsplit

from src.store import now_iso

NOT_AUTHENTICATED = 'NOT_AUTHENTICATED'
LOGIN_IN_PROGRESS = 'LOGIN_IN_PROGRESS'
AUTHENTICATED = 'AUTHENTICATED'
NEEDS_LOGIN = 'NEEDS_LOGIN'
STUDIO_URL = 'https://www.tiktok.com/tiktokstudio'
LOGIN_URL = 'https://www.tiktok.com/login?redirect_url=https%3A%2F%2Fwww.tiktok.com%2Ftiktokstudio'


def normalize_handle(value):
    return str(value or '').strip().lstrip('@').casefold()


def classify_auth_layers(current_url, session_cookie_present, *, login_ui_present,
                         authenticated_dom_marker_present, dom_collected,
                         detector_error=False, blocked=False, expected_handle='',
                         observed_handles=(), profile='', confirmed_profile='',
                         confirmed_handle='', auth_dom_markers=()):
    """Classify structured browser signals without depending on console output."""
    handles = sorted({normalize_handle(handle) for handle in observed_handles if normalize_handle(handle)})
    positive = (is_studio_url(current_url) and session_cookie_present is True
                and authenticated_dom_marker_present is True and not login_ui_present
                and not blocked and not detector_error and dom_collected is True)
    expected = normalize_handle(expected_handle)
    identity = 'ACCOUNT_IDENTITY_UNKNOWN'
    if expected and handles:
        identity = 'ACCOUNT_CONFIRMED' if handles == [expected] else 'ACCOUNT_MISMATCH'
    elif expected and profile and profile == confirmed_profile and normalize_handle(confirmed_handle) == expected:
        identity = 'ACCOUNT_CONFIRMED'
    return {
        'auth_result': AUTHENTICATED if positive else 'UNKNOWN',
        'account_identity': identity,
        'observed_handles': handles,
        'auth_dom_markers': list(auth_dom_markers),
        'login_ui_found': bool(login_ui_present),
    }


def evaluate_studio_snapshot(snapshot, expected_handle='', *, profile='', confirmed_profile='', confirmed_handle=''):
    page = next((p for p in snapshot.get('pages', []) if p.get('is_auth_page')), {})
    inventory = page.get('studio_dom_inventory') or []
    def normalized(value):
        return ''.join(c for c in unicodedata.normalize('NFD', str(value or '').casefold())
                       if not unicodedata.combining(c)).strip()
    buttons = [normalized(x.get('text') or x.get('aria_label')) for x in inventory
               if x.get('tag') == 'button' or x.get('role') == 'button']
    # The observed home profile precedes the metrics section. Comment-author links
    # below it are not evidence of the currently signed-in account.
    boundary = next((i for i,x in enumerate(inventory) if normalized(x.get('text') or x.get('aria_label')) in {'metricas principais','key metrics'}), None)
    account_nodes = inventory[:boundary] if boundary is not None else [x for x in inventory if x.get('identity_scope') == 'account']
    handles = {normalize_handle(x.get('text')) for x in account_nodes if x.get('tag') == 'a'
               and re.fullmatch(r'@?[A-Za-z0-9_.]{2,24}', str(x.get('text', '')).strip())}
    metrics = any(x in {'metricas principais', 'key metrics'} for x in buttons)
    video = any(x.startswith(('visualizacoes de video', 'video views')) for x in buttons)
    recent = any(x in {'publicacoes recentes', 'recent posts'} for x in buttons)
    navigation = len({x.get('test_id') for x in inventory} & {'House', 'Folder', 'ChartRise', 'Comments'}) >= 3
    analytics = (urlsplit(page.get('url') or '').path.startswith('/tiktokstudio/analytics/')
                 and 'baixar dados' in buttons and 'ultimos 7 dias' in buttons
                 and any(x == 'ver dados' for x in buttons))
    login = page.get('login_control_visible') is True or any(x in {'entrar','entrar no tiktok','log in','login','sign in'} for x in buttons)
    markers = [name for name, found in [('dashboard_metrics', metrics and video),
        ('recent_posts', recent), ('profile_link', bool(handles)), ('studio_navigation', navigation), ('analytics_controls', analytics),
        ('logout_control', page.get('logout_control_visible') is True)] if found]
    # Internal Studio routes may omit the dashboard cards and account handle.
    # The authenticated navigation shell is a stable positive DOM marker on
    # those routes and is still gated by URL, session cookie, and no login UI.
    shell = navigation or (metrics and video and (recent or bool(handles))) or (analytics and navigation) or page.get('logout_control_visible') is True
    blocked = set(page.get('semantic_signals', {}).get('visible', [])) & {'CHALLENGE','RATE_LIMITED','LOGIN_REJECTED','ERROR'}
    return classify_auth_layers(
        page.get('url') or '', snapshot.get('session_cookie_present'),
        login_ui_present=login, authenticated_dom_marker_present=shell,
        dom_collected=page.get('dom_collected') is True,
        detector_error=bool(snapshot.get('detector_error')), blocked=bool(blocked),
        expected_handle=expected_handle, observed_handles=handles,
        profile=profile, confirmed_profile=confirmed_profile,
        confirmed_handle=confirmed_handle, auth_dom_markers=markers)


def is_tiktok_url(url):
    parsed = urlsplit(url)
    return parsed.scheme == 'https' and parsed.hostname == 'www.tiktok.com'


def is_login_url(url):
    return is_tiktok_url(url) and urlsplit(url).path.rstrip('/').split('/')[1:2] == ['login']


def is_studio_url(url):
    return is_tiktok_url(url) and urlsplit(url).path.rstrip('/').split('/')[1:2] == ['tiktokstudio']


@dataclass(frozen=True)
class AuthObservation:
    positive: bool = False
    login_required: bool = False
    evidence: str = 'Aguardando confirmacao positiva na pagina'
    location: str = ''
    account_identity: str = 'ACCOUNT_IDENTITY_UNKNOWN'


class TikTokAuthDetector:
    # Semantic controls, not QR state or a timer. Cookie values never leave this detector.
    DOM_SIGNALS = r"""() => {
      const visible = el => !!(el.getClientRects().length) &&
        getComputedStyle(el).visibility !== 'hidden';
      const text = el => (el.innerText || el.getAttribute('aria-label') || '')
        .normalize('NFD').replace(/[\u0300-\u036f]/g, '').trim().toLowerCase();
      const controls = [...document.querySelectorAll('a,button,[role="button"],[role="menuitem"]')].filter(visible);
      const body = text(document.body);
      return {
        url: location.href,
        inventory: controls.map(el => ({tag:el.tagName.toLowerCase(),role:el.getAttribute('role'),
          text:(el.innerText || '').slice(0,160),aria_label:el.getAttribute('aria-label')})).concat(
          [...document.querySelectorAll('[data-testid],[data-e2e]')].filter(visible).map(el =>
            ({test_id:el.getAttribute('data-testid') || el.getAttribute('data-e2e')}))),
        logout: controls.some(el => /^(log out|logout|sign out|sair|sair da conta)$/.test(text(el))),
        login: controls.some(el => /^(log in|login|sign in|entrar|entrar no tiktok)$/.test(text(el))),
        connected: /voce esta conectado|nao e preciso repetir|already logged in|no need to log in again/.test(body),
        challenge: /verify you are human|verifique se voce e humano|verification code|codigo de verificacao|too many attempts|muitas tentativas/.test(body)
      };
    }"""

    def observe(self, page, account=None):
        signals = page.evaluate(self.DOM_SIGNALS)
        url = signals['url']
        if not is_tiktok_url(url):
            return AuthObservation(evidence='Aguardando retorno da autenticacao manual')
        path = urlsplit(url).path  # Do not persist query strings or OAuth tokens.
        if signals.get('challenge'):
            return AuthObservation(evidence='Verificacao manual ou limite informado na pagina', location=path)
        cookies = page.context.cookies(['https://www.tiktok.com'])
        session = any(c.get('name') in {'sessionid', 'sessionid_ss', 'sid_tt'}
                      and c.get('domain', '').lstrip('.') in {'tiktok.com', 'www.tiktok.com'}
                      and bool(c.get('value'))
                      and (c.get('expires', -1) == -1 or c.get('expires', 0) > time())
                      for c in cookies)
        result = evaluate_studio_snapshot({'session_cookie_present': session, 'pages': [{
            'is_auth_page': True, 'url': url, 'dom_collected': True,
            'studio_dom_inventory': signals.get('inventory', []),
            'login_control_visible': signals.get('login'), 'logout_control_visible': signals.get('logout')}]},
            (account or {}).get('tiktok_username', ''), profile=(account or {}).get('browser_profile', ''),
            confirmed_profile=(account or {}).get('identity_confirmed_profile', ''),
            confirmed_handle=(account or {}).get('identity_confirmed_handle', ''))
        if result['account_identity'] == 'ACCOUNT_MISMATCH':
            return AuthObservation(evidence='Identidade divergente; operacoes bloqueadas', location=path, account_identity='ACCOUNT_MISMATCH')
        if result['auth_result'] == AUTHENTICATED:
            return AuthObservation(True, evidence='Sessao e shell autenticado do Studio confirmados', location=path, account_identity=result['account_identity'])
        return AuthObservation(login_required=is_login_url(url) or bool(signals.get('login')),
                               location=path)


@dataclass
class LoginSession:
    status: str = NOT_AUTHENTICATED
    page: object = None
    queued: bool = False
    manual_pending: bool = False
    navigation_started: bool = False
    verification_only: bool = False
    verification_navigation_used: bool = False
    blocked_auth_navigation: bool = False
    positive_key: tuple = None
    positive_readings: int = 0

    def detect(self, observation):
        if observation.positive:
            key = (observation.location, observation.evidence)
            self.positive_readings = self.positive_readings + 1 if key == self.positive_key else 1
            self.positive_key = key
            if self.positive_readings >= 2:
                self.status = AUTHENTICATED
                self.manual_pending = False
        else:
            self.positive_key, self.positive_readings = None, 0
            if self.manual_pending:
                self.status = LOGIN_IN_PROGRESS
            elif observation.login_required:
                self.status = NEEDS_LOGIN
            else:
                self.status = NOT_AUTHENTICATED
        return self.status


def persist_auth(conn, account_id, status, detail):
    if status not in {AUTHENTICATED, NEEDS_LOGIN}:
        return
    verified = int(status == AUTHENTICATED)
    row = conn.execute('SELECT auth_status,session_verified,auth_detail FROM publisher_accounts WHERE id=?',
                       (account_id,)).fetchone()
    if row and tuple(row) != (status, verified, detail):
        timestamp = now_iso()
        with conn:
            conn.execute('''UPDATE publisher_accounts SET auth_status=?,session_verified=?,auth_detail=?,
              session_verified_at=CASE WHEN ?=1 AND session_verified=0 THEN ? ELSE session_verified_at END,
              updated_at=? WHERE id=?''', (status, verified, detail, verified, timestamp, timestamp, account_id))


def recover_auth(conn):
    # A persisted success is historical evidence; interrupted attempts are never resumed.
    with conn:
        conn.execute("""UPDATE publisher_accounts SET auth_status=CASE WHEN session_verified=1 THEN 'AUTHENTICATED' ELSE 'NOT_AUTHENTICATED' END,
          auth_detail='Runtime reiniciado; verificacao pendente' WHERE auth_status='LOGIN_IN_PROGRESS'""")
