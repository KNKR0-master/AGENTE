"""Read-only page selection inside the worker-owned persistent context."""

from urllib.parse import urlsplit

from src.publisher.auth import AUTHENTICATED, STUDIO_URL, is_login_url, is_studio_url


def classify_verification_auth(snapshot, auth, page):
    selected = next((item for item in snapshot.get('pages', []) if item.get('is_auth_page')), {})
    signals = set(selected.get('semantic_signals', {}).get('visible', []))
    if signals & {'CHALLENGE', 'LOGIN_REJECTED', 'RATE_LIMITED'}:
        return 'SECURITY_CHALLENGE'
    if auth.get('account_identity') == 'ACCOUNT_MISMATCH':
        return 'WRONG_ACCOUNT'
    if auth.get('auth_result') == AUTHENTICATED:
        return None
    if is_login_url(page.url) or auth.get('login_ui_found') is True:
        return 'AUTH_REQUIRED'
    return 'LISTING_ERROR'


def select_verification_authority(context, inspect):
    """Never launch/attach a browser or navigate an existing user's page.

    inspect(page) returns the passive classifier result with blocker_code.
    An owned page is returned only when Studio must be opened in this context.
    """
    pages = [page for page in context.pages if not page.is_closed()]
    editors = [page for page in pages if is_studio_url(page.url) and
               urlsplit(page.url).path.startswith(('/tiktokstudio/upload', '/tiktokstudio/edit'))]
    if editors:
        return None, False, {'blocker_code': 'UNSAVED_EDITOR'}
    studios = [page for page in pages if is_studio_url(page.url)]
    studios.sort(key=lambda page: urlsplit(page.url).path.rstrip('/') != '/tiktokstudio')
    for page in studios:
        try:
            auth = inspect(page)
        except Exception as exc:
            if type(exc).__name__ == 'TargetClosedError' and page.is_closed():
                continue
            raise
        if auth.get('blocker_code') in ('WRONG_ACCOUNT', 'SECURITY_CHALLENGE', 'CAPTCHA', 'TWO_FACTOR', 'UNSAVED_EDITOR'):
            return None, False, auth
        if (auth.get('auth_result') == AUTHENTICATED and
                auth.get('account_identity') == 'ACCOUNT_CONFIRMED' and
                len(auth.get('observed_handles') or []) == 1):
            return page, False, auth
    page = context.new_page()
    try:
        page.goto(STUDIO_URL, wait_until='domcontentloaded', timeout=30000)
        # Bound DOM hydration reads; never retry navigation or a remote mutation.
        for attempt in range(5):
            auth = inspect(page)
            if auth.get('blocker_code') != 'LISTING_ERROR' or attempt == 4:
                break
            page.wait_for_timeout(500)
        return page, True, auth
    except Exception:
        if not page.is_closed():
            page.close()
        raise
