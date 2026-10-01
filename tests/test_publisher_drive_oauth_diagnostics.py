import json
import traceback
import unittest
from unittest.mock import MagicMock, patch

from src.google_drive_auth import (CECILIA, DRIVE_SCOPE, DriveAuthError,
                                   SafeAuthCause, save_authorized_user)
from src.google_drive_oauth_diagnostics import run_instrumented_local_server


class FakeFlow:
    def __init__(self, *, fail_at=None):
        self.fail_at = fail_at
        self.credentials = MagicMock(token='access-secret', refresh_token='refresh-secret')

    def authorization_url(self, **_options):
        return 'https://example.invalid/?state=state-secret', 'state-secret'

    def fetch_token(self, **_options):
        if self.fail_at == 'token':
            raise ValueError('authorization-code-secret')

    def run_local_server(self, **_options):
        import google_auth_oauthlib.flow as flow_module
        if self.fail_at == 'bind':
            raise OSError('path-secret')
        self.authorization_url()
        flow_module.webbrowser.get().open('https://example.invalid/?code=authorization-code-secret',
                                          new=1, autoraise=True)
        if self.fail_at == 'callback':
            raise TimeoutError('state-secret')
        self.fetch_token(authorization_response='https://localhost/?code=authorization-code-secret')
        return self.credentials


class OAuthDiagnosticTests(unittest.TestCase):
    def setUp(self):
        self.events = []
        self.controller = MagicMock()
        self.controller.open.return_value = True

    def run_flow(self, flow):
        with patch('webbrowser.get', return_value=self.controller):
            return run_instrumented_local_server(flow, event_sink=self.events.append,
                                                 host='localhost', bind_addr='127.0.0.1', port=0,
                                                 open_browser=True, authorization_prompt_message=None)

    def test_browser_callback_and_token_stages_are_secret_free(self):
        self.run_flow(FakeFlow())
        stages = [event['stage'] for event in self.events]
        self.assertEqual(stages, ['OAUTH_FLOW_CREATED', 'LOCAL_SERVER_STARTED',
                                  'BROWSER_OPEN_ATTEMPTED', 'BROWSER_OPEN_RESULT',
                                  'CALLBACK_RECEIVED', 'TOKEN_EXCHANGE_COMPLETED'])
        self.assertTrue(self.events[3]['browser_open_result'])
        self.assertTrue(self.events[-1]['access_token_present'])
        self.assertTrue(self.events[-1]['refresh_token_present'])
        self.assertNotIn('secret', json.dumps(self.events))

    def test_browser_false_and_exception_are_observed(self):
        self.controller.open.return_value = False
        self.run_flow(FakeFlow())
        self.assertFalse(next(e for e in self.events if e['stage'] == 'BROWSER_OPEN_RESULT')['browser_open_result'])
        self.events.clear()
        self.controller.open.side_effect = OSError('state-secret')
        with self.assertRaises(DriveAuthError) as caught:
            self.run_flow(FakeFlow())
        self.assertEqual(caught.exception.code, 'BROWSER_OPEN_FAILED')
        self.assertFalse(next(e for e in self.events if e['stage'] == 'BROWSER_OPEN_RESULT')['browser_open_result'])
        self.assertNotIn('secret', json.dumps(self.events) + str(caught.exception) + str(caught.exception.__cause__))

    def test_callback_and_token_failure_codes(self):
        with self.assertRaises(DriveAuthError) as callback:
            self.run_flow(FakeFlow(fail_at='callback'))
        self.assertEqual((callback.exception.code, callback.exception.stage),
                         ('OAUTH_CALLBACK_FAILED', 'LOCAL_CALLBACK'))
        self.assertFalse(any(e['stage'] == 'CALLBACK_RECEIVED' for e in self.events))
        self.events.clear()
        with self.assertRaises(DriveAuthError) as token:
            self.run_flow(FakeFlow(fail_at='token'))
        self.assertEqual((token.exception.code, token.exception.stage),
                         ('TOKEN_EXCHANGE_FAILED', 'TOKEN_EXCHANGE'))
        self.assertTrue(any(e['stage'] == 'CALLBACK_RECEIVED' for e in self.events))
        self.assertNotIn('secret', json.dumps(self.events) + str(token.exception.__cause__))

    def test_bind_failure_is_classified_without_browser(self):
        with self.assertRaises(DriveAuthError) as caught:
            self.run_flow(FakeFlow(fail_at='bind'))
        self.assertEqual(caught.exception.code, 'LOCAL_SERVER_BIND_FAILED')
        self.controller.open.assert_not_called()

    def test_save_stages_and_failures_preserve_old_token(self):
        credentials = MagicMock()
        credentials.granted_scopes = [DRIVE_SCOPE]
        credentials.has_scopes.return_value = True
        credentials.refresh_token = 'refresh-secret'
        credentials.to_json.return_value = json.dumps({'refresh_token': 'refresh-secret', 'token': 'access-secret'})
        service = MagicMock()
        service.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': CECILIA, 'me': True}}
        builder = MagicMock(return_value=service)
        persisted = {'old': 'old-secret'}
        store = MagicMock()
        store.save.side_effect = lambda value: persisted.update(value)

        credentials.refresh_token = None
        with self.assertRaises(DriveAuthError) as missing:
            save_authorized_user(credentials, 'unused', builder=builder, store=store,
                                 event_sink=self.events.append)
        self.assertEqual((missing.exception.code, missing.exception.stage),
                         ('REFRESH_TOKEN_MISSING', 'REFRESH_TOKEN_CHECK'))
        self.assertEqual(persisted, {'old': 'old-secret'})
        self.assertFalse(any(e['stage'] == 'DPAPI_SAVE_STARTED' for e in self.events))

        credentials.refresh_token = 'refresh-secret'
        self.events.clear()
        service.about.return_value.get.return_value.execute.side_effect = RuntimeError('client-secret')
        with self.assertRaises(DriveAuthError) as api:
            save_authorized_user(credentials, 'unused', builder=builder, store=store,
                                 event_sink=self.events.append)
        self.assertEqual((api.exception.code, api.exception.stage),
                         ('ACCOUNT_VALIDATION_FAILED', 'ACCOUNT_VALIDATION'))
        self.assertIsInstance(api.exception.__cause__, SafeAuthCause)
        self.assertNotIn('client-secret', ''.join(traceback.format_exception(api.exception)))
        self.assertEqual(persisted, {'old': 'old-secret'})

        service.about.return_value.get.return_value.execute.side_effect = None
        service.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': 'other@example.com', 'me': True}}
        with self.assertRaises(DriveAuthError) as mismatch:
            save_authorized_user(credentials, 'unused', builder=builder, store=store,
                                 event_sink=self.events.append)
        self.assertEqual(mismatch.exception.code, 'ACCOUNT_MISMATCH')
        self.assertEqual(persisted, {'old': 'old-secret'})

        service.about.return_value.get.return_value.execute.return_value = {
            'user': {'emailAddress': CECILIA, 'me': True}}
        store.save.side_effect = OSError('dpapi-secret')
        with self.assertRaises(DriveAuthError) as dpapi:
            save_authorized_user(credentials, 'unused', builder=builder, store=store,
                                 event_sink=self.events.append)
        self.assertEqual((dpapi.exception.code, dpapi.exception.stage), ('DPAPI_SAVE_FAILED', 'DPAPI_SAVE'))
        self.assertEqual(persisted, {'old': 'old-secret'})
        self.assertNotIn('secret', json.dumps(self.events) + ''.join(traceback.format_exception(dpapi.exception)))

        self.events.clear()
        store.save.side_effect = lambda value: persisted.update(value)
        save_authorized_user(credentials, 'unused', builder=builder, store=store,
                             event_sink=self.events.append)
        self.assertEqual(self.events[-1]['stage'], 'AUTH_COMPLETED')
        self.assertIn('DPAPI_SAVE_COMPLETED', [e['stage'] for e in self.events])
        self.assertNotIn('secret', json.dumps(self.events))
