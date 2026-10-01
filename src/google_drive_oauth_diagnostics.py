"""Opt-in, secret-free stage observation for one Desktop OAuth attempt."""

import threading
from unittest.mock import patch

from src.google_drive_auth import DriveAuthError, _auth_event, safe_auth_cause

_FLOW_LOCK = threading.Lock()


def run_instrumented_local_server(flow, *, event_sink=None, **kwargs):
    """Delegate OAuth to InstalledAppFlow; observe its real browser/callback path."""
    if kwargs.get('authorization_prompt_message') not in (None, ''):
        raise ValueError('AUTHORIZATION_URL_CONSOLE_OUTPUT_FORBIDDEN')
    kwargs['authorization_prompt_message'] = None
    _auth_event(event_sink, 'OAUTH_FLOW_CREATED')
    original_authorization_url = flow.authorization_url
    original_fetch_token = flow.fetch_token
    server_started = False
    callback_received = False
    browser_failed = False

    def observed_authorization_url(*args, **options):
        nonlocal server_started
        server_started = True
        _auth_event(event_sink, 'LOCAL_SERVER_STARTED')
        return original_authorization_url(*args, **options)

    def observed_fetch_token(*args, **options):
        nonlocal callback_received
        callback_received = True
        _auth_event(event_sink, 'CALLBACK_RECEIVED', callback_received=True)
        try:
            result = original_fetch_token(*args, **options)
        except Exception as exc:
            cause = safe_auth_cause(exc)
            _auth_event(event_sink, 'TOKEN_EXCHANGE_FAILED', exception_type=cause.exception_type,
                        http_status=cause.http_status, reason=cause.reason)
            raise DriveAuthError('TOKEN_EXCHANGE_FAILED', code='TOKEN_EXCHANGE_FAILED',
                                 stage='TOKEN_EXCHANGE') from cause
        credentials = flow.credentials
        _auth_event(event_sink, 'TOKEN_EXCHANGE_COMPLETED',
                    access_token_present=bool(credentials.token),
                    refresh_token_present=bool(credentials.refresh_token))
        return result

    class ObservedBrowser:
        def __init__(self, controller):
            self.controller = controller

        def open(self, url, **options):
            nonlocal browser_failed
            _auth_event(event_sink, 'BROWSER_OPEN_ATTEMPTED', controller_resolved=True)
            try:
                opened = bool(self.controller.open(url, **options))
            except Exception as exc:
                browser_failed = True
                _auth_event(event_sink, 'BROWSER_OPEN_RESULT', browser_open_result=False,
                            exception_type=type(exc).__name__)
                raise
            _auth_event(event_sink, 'BROWSER_OPEN_RESULT', browser_open_result=opened)
            return opened

    with _FLOW_LOCK:
        import google_auth_oauthlib.flow as flow_module
        original_get = flow_module.webbrowser.get

        def observed_get(browser=None):
            try:
                return ObservedBrowser(original_get(browser))
            except Exception as exc:
                _auth_event(event_sink, 'BROWSER_OPEN_RESULT', controller_resolved=False,
                            browser_open_result=False, exception_type=type(exc).__name__)
                raise

        flow.authorization_url = observed_authorization_url
        flow.fetch_token = observed_fetch_token
        try:
            with patch('google_auth_oauthlib.flow.webbrowser.get', side_effect=observed_get):
                try:
                    return flow.run_local_server(**kwargs)
                except DriveAuthError:
                    raise
                except Exception as exc:
                    code = ('BROWSER_OPEN_FAILED' if browser_failed
                            else 'OAUTH_CALLBACK_FAILED' if server_started and not callback_received
                            else 'LOCAL_SERVER_BIND_FAILED' if not server_started
                            else 'UNKNOWN_AUTH_FAILURE')
                    stage = ('LOCAL_CALLBACK' if code == 'OAUTH_CALLBACK_FAILED' else
                             'BROWSER_OPEN' if code == 'BROWSER_OPEN_FAILED' else
                             'LOCAL_SERVER' if code == 'LOCAL_SERVER_BIND_FAILED' else 'OAUTH_FLOW')
                    cause = safe_auth_cause(exc)
                    _auth_event(event_sink, code, exception_type=cause.exception_type,
                                http_status=cause.http_status, reason=cause.reason)
                    raise DriveAuthError(code, code=code, stage=stage) from cause
        finally:
            flow.authorization_url = original_authorization_url
            flow.fetch_token = original_fetch_token
