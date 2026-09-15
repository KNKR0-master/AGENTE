"""Publisher UI fixtures only. Offline Chrome, every request intercepted, no live API."""
import json
import sys
from pathlib import Path
from urllib.parse import urlsplit

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
from playwright.sync_api import sync_playwright, expect
from src.publisher.auth import TikTokAuthDetector


def main():
    qa = ROOT / 'data/publisher/qa'
    qa.mkdir(parents=True, exist_ok=True)
    account = {'id': 1, 'name': 'CECILIA FIGUEIREDO (fixture)', 'active': 1,
               'browser_profile': 'data/publisher/browser_profiles/fixture-only',
               'auth_status': 'NOT_AUTHENTICATED', 'session_verified': 0, 'login_active': False}
    state = dict(drafts=[], products=[], accounts=[account], presets=[], batches=[], logs=[],
                 review_sessions=[], statuses=['DRAFT'], media_assets=[], executions=[],
                 worker_config={'tiktok_actions_paused': True})
    pending, posts, external, errors = [], [], [], []
    with sync_playwright() as p:
        browser = p.chromium.launch(channel='chrome', headless=True)
        try:
            context = browser.new_context(offline=True, service_workers='block', viewport={'width': 1440, 'height': 1000})
            def route_request(route):
                request = route.request
                url = urlsplit(request.url)
                if url.hostname != 'publisher.fixture':
                    external.append(request.url)
                    route.abort()
                elif url.path == '/api/publisher/login':
                    posts.append(request.post_data_json)
                    pending.append(route)  # No backend and no authentication request leaves Chrome.
                elif url.path == '/api/publisher':
                    route.fulfill(json=state)
                elif url.path in {'/publisher.html', '/publisher.js', '/publisher.css'}:
                    kind = {'html':'text/html', 'js':'application/javascript', 'css':'text/css'}[url.path.rsplit('.', 1)[-1]]
                    route.fulfill(path=str(ROOT / 'dashboard' / url.path.lstrip('/')), content_type=kind)
                else:
                    route.fulfill(status=404, body='fixture only')
            context.route('**/*', route_request)
            page = context.new_page()
            page.on('pageerror', lambda error: errors.append(str(error)))
            page.goto('http://publisher.fixture/publisher.html')
            page.locator('nav [data-view=accounts]').click()
            button = page.locator('[data-login="1"]')
            expect(button).to_be_disabled()
            expect(page.locator('#accountList')).to_contain_text('Sess\u00e3o TikTok: n\u00e3o verificada')
            state['worker_config']['tiktok_actions_paused'] = False
            page.evaluate('refresh()')
            expect(button).to_be_enabled()
            expect(button).to_have_text('Abrir login')
            assert not posts, 'Loading/refreshing the dashboard must never submit login'
            page.evaluate('() => { openAccountLogin(1); openAccountLogin(1); }')
            expect(button).to_be_disabled()
            expect(button).to_have_attribute('aria-busy', 'true')
            page.wait_for_timeout(30)
            assert len(posts) == 1, posts
            account.update(auth_status='LOGIN_IN_PROGRESS', login_active=True)
            pending.pop().fulfill(json={'ok': True, 'code': 'LOGIN_STARTED', **account})
            expect(button).to_have_text('Continuar login')
            expect(button).to_be_enabled()
            expect(page.locator('#accountList')).to_contain_text('login em andamento')
            button.click()
            page.wait_for_timeout(30)
            assert len(posts) == 2
            pending.pop().fulfill(json={'ok': True, 'code': 'LOGIN_ALREADY_IN_PROGRESS', **account})
            expect(page.locator('#notice')).to_contain_text('Reutilizando a mesma aba')
            for status, label, active, text in [
                ('AUTHENTICATED', 'autenticada', True, 'Abrir TikTok Studio'),
                ('NEEDS_LOGIN', 'requer login', True, 'Continuar login'),
                ('NEEDS_LOGIN', 'requer login', False, 'Abrir login'),
                ('NOT_AUTHENTICATED', 'n\u00e3o verificada', False, 'Abrir login'),
            ]:
                account.update(auth_status=status, login_active=active)
                page.evaluate('refresh()')
                expect(button).to_have_text(text)
                expect(page.locator('#accountList')).to_contain_text('Sess\u00e3o TikTok: ' + label)
            account.update(auth_status='AUTHENTICATED', session_verified=1,
                           session_verified_at='2026-09-08T00:00:00+00:00',
                           auth_detail='Sessao e area autenticada confirmadas')
            page.evaluate('refresh()')
            for width in [1440, 768, 390]:
                page.set_viewport_size({'width': width, 'height': 900})
                assert page.evaluate('document.documentElement.scrollWidth <= innerWidth'), 'Page overflow'
                assert button.evaluate('el => el.scrollWidth <= el.clientWidth'), 'Button overflow'
                page.screenshot(path=str(qa / f'auth-offline-{width}.png'), full_page=True)
            # Verify actual DOM projection too, with inert local HTML, not TikTok.
            page.set_content('<button>Sair</button><button hidden>Entrar</button>')
            signals = page.evaluate(TikTokAuthDetector.DOM_SIGNALS)
            assert signals['logout'] and not signals['login']
            page.set_content('<button>Log in</button><p>Verification code</p>')
            signals = page.evaluate(TikTokAuthDetector.DOM_SIGNALS)
            assert signals['login'] and signals['challenge']
            assert len(posts) == 2, posts
            assert not external, external
            assert not errors, errors
            print('PASS: offline UI states, debounce, reused-session feedback, passive DOM signals, 3 viewports; zero external requests')
        finally:
            browser.close()


if __name__ == '__main__':
    main()
