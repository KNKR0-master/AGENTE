"""One explicitly authorized attempt; passive evidence, no browser actions here."""
import json
from time import monotonic

from src.store import now_iso


class LoginTrace:
    def __init__(self, path, clock=monotonic):
        self.path, self.clock = path, clock
        self.started = clock()
        self.confirmed_at = None
        self.last_state = self.last_auth = self.last_url = self.last_storage = None
        self.opened = self.qr_seen = False
        self.stopped = False
        self.result = None
        self.stop_reason = None
        self.last_snapshot = None
        self.positive_samples = 0
        self.path.parent.mkdir(parents=True, exist_ok=True)

    def write(self, kind, data):
        with self.path.open('a', encoding='utf-8') as stream:
            stream.write(json.dumps({'timestamp': now_iso(), 'kind': kind, **data}, ensure_ascii=True) + '\n')

    def event(self, event):
        if self.stopped:
            return
        self.write('EVENT', event)
        if event.get('status') == 429:
            self.stop_reason = 'RATE_LIMITED'
        elif event.get('event') in {'PAGE_CLOSED', 'CONTEXT_CLOSED', 'POPUP'}:
            self.stop_reason = self.stop_reason or 'UNKNOWN'

    def phone_confirmed(self):
        # Explicit human annotation, never inferred from an app confirmation claim in DOM.
        if not self.stopped and self.confirmed_at is None:
            self.confirmed_at = self.clock()
            self.write('MARKER', {'event': 'PHONE_CONFIRMATION_REPORTED', 'source': 'USER'})

    def snapshot(self, reason, snapshot):
        self.write('SNAPSHOT', {'reason': reason, 'snapshot': snapshot})

    def observe(self, snapshot):
        if self.stopped:
            return self.result
        self.last_snapshot = snapshot
        page = next((p for p in snapshot['pages'] if p['is_auth_page']), {})
        state = page.get('login_state', 'UNKNOWN')
        auth = snapshot['diagnostic_state']
        url = page.get('url')
        if page.get('is_login_page') and not self.opened:
            self.opened = True
            self.snapshot('LOGIN_PAGE_OPENED', snapshot)
        if state == 'QR_WAITING_SCAN' and not self.qr_seen:
            self.qr_seen = True
            self.snapshot('QR_AVAILABLE', snapshot)
        if state != self.last_state:
            self.snapshot('QR_STATE_CHANGED', snapshot)
        if state == 'CONFIRMATION_COMPLETE' and self.confirmed_at is None:
            self.confirmed_at = self.clock()
            self.write('MARKER', {'event': 'DOM_CONFIRMATION_COMPLETE', 'source': 'DOM'})
        if self.confirmed_at is not None and self.last_url and url != self.last_url:
            self.snapshot('NAVIGATION_AFTER_CONFIRMATION', snapshot)
        if auth != self.last_auth:
            self.snapshot('AUTH_STATE_CHANGED', snapshot)
        storage = [snapshot.get('cookies'), [(p.get('localStorageKeys'), p.get('sessionStorageKeys'),
                   p.get('indexedDB'), p.get('serviceWorkers')) for p in snapshot['pages']]]
        if storage != self.last_storage:
            self.snapshot('COOKIE_STORAGE_METADATA_CHANGED', snapshot)
        self.last_state, self.last_auth, self.last_url, self.last_storage = state, auth, url, storage
        self.positive_samples = self.positive_samples + 1 if auth == 'AUTHENTICATED' else 0
        if state in {'RATE_LIMITED', 'CHALLENGE', 'LOGIN_REJECTED'}:
            self.stop_reason = state
        if snapshot.get('page_count') != 1 or snapshot.get('context_count') != 1:
            self.stop_reason = self.stop_reason or 'UNKNOWN'
        expired = self.clock() - self.started >= 300
        confirmed_timeout = self.confirmed_at is not None and self.clock() - self.confirmed_at >= 60
        if self.stop_reason or self.positive_samples >= 2 or expired or confirmed_timeout or state == 'QR_EXPIRED':
            result = self.stop_reason or ('AUTHENTICATED' if self.positive_samples >= 2 else
                auth if auth in {'AUTH_NOT_RECEIVED', 'AUTH_STATE_PRESENT_BUT_PAGE_STUCK'} else 'UNKNOWN')
            self.finish(result, snapshot)
        return self.result

    def finish(self, result, snapshot):
        if not self.stopped:
            self.result = result
            self.snapshot('FINAL_STATE', snapshot)
            self.write('RESULT', {'classification': result})
            self.stopped = True
