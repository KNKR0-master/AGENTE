"""Explicit, single-flight Desktop OAuth for the Publisher Drive account."""

import json
import os
import tempfile
import threading
from pathlib import Path

from src.google_drive_auth import (CECILIA, DRIVE_SCOPE, DriveAuthError,
                                   DriveCredentialStore, save_authorized_user, user_drive_service)
from src.google_drive_oauth_diagnostics import run_instrumented_local_server


class DriveReauthManager:
    def __init__(self, root, poller, *, flow_factory=None, runner=run_instrumented_local_server,
                 saver=save_authorized_user, store_factory=DriveCredentialStore,
                 timeout_seconds=600):
        self.root = Path(root)
        self.poller = poller
        self.flow_factory = flow_factory or self._new_flow
        self.runner = runner
        self.saver = saver
        self.store_factory = store_factory
        self.timeout_seconds = timeout_seconds
        self._guard = threading.Lock()
        self._thread = None
        self._state = 'AUTH_REQUIRED'
        self._error_code = None
        self._error_stage = None
        self._last_stage = None

    @staticmethod
    def _new_flow(client, scope):
        from google_auth_oauthlib.flow import InstalledAppFlow
        return InstalledAppFlow.from_client_config(
            client, scopes=[scope], autogenerate_code_verifier=True)

    def status(self):
        with self._guard:
            return {'state': self._state, 'error_code': self._error_code,
                    'error_stage': self._error_stage, 'last_stage': self._last_stage}

    def hydrate(self, *, service_loader=None):
        """Validate persisted authentication without initiating interactive consent."""
        with self._guard:
            if self._thread and self._thread.is_alive():
                return self._snapshot_locked()
            try:
                settings = json.loads((self.root / 'config/cecilia_drive_auth.json').read_text(encoding='utf-8'))
                if (settings.get('auth_mode') != 'user_oauth' or
                        settings.get('expected_google_account') != CECILIA or
                        settings.get('scopes') != [DRIVE_SCOPE]):
                    raise ValueError('Invalid authentication configuration')
                store_path = (self.root / settings['credential_store']).resolve()
                if not store_path.is_relative_to(self.root.resolve()):
                    raise ValueError('Invalid credential path')
                (service_loader or user_drive_service)(
                    store_path, CECILIA, store=self.store_factory(store_path))
            except Exception:
                self._state = 'AUTH_REQUIRED'
                self._error_code = 'PERSISTED_CREDENTIAL_UNUSABLE'
                self._error_stage = 'STARTUP_CREDENTIAL_VALIDATION'
            else:
                self._state = 'READY'
                self._error_code = self._error_stage = None
            return self._snapshot_locked()

    def start(self, *, polling):
        if not (polling.get('enabled') and polling.get('status') == 'PAUSED'
                and polling.get('paused_reason') == 'AUTH_REQUIRED'):
            raise ValueError('DRIVE_REAUTH_REQUIRES_AUTH_REQUIRED')
        with self._guard:
            if self._state == 'READY' or (self._thread and self._thread.is_alive()):
                return {'started': False, **self._snapshot_locked()}
            self._state = 'REAUTHORIZING'
            self._error_code = self._error_stage = self._last_stage = None
            self._thread = threading.Thread(target=self._run, name='publisher-drive-reauth', daemon=True)
            self._thread.start()
            return {'started': True, **self._snapshot_locked()}

    def _snapshot_locked(self):
        return {'state': self._state, 'error_code': self._error_code,
                'error_stage': self._error_stage, 'last_stage': self._last_stage}

    def _event(self, event):
        with self._guard:
            self._last_stage = event['stage']

    @staticmethod
    def _restore_previous(store_path, previous):
        if previous is None:
            store_path.unlink(missing_ok=True)
            return
        fd, temporary = tempfile.mkstemp(prefix='.credential-rollback-', dir=store_path.parent)
        try:
            with os.fdopen(fd, 'wb') as stream:
                stream.write(previous)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, store_path)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def _run(self):
        try:
            settings = json.loads((self.root / 'config/cecilia_drive_auth.json').read_text(encoding='utf-8'))
            if (settings.get('auth_mode') != 'user_oauth' or
                    settings.get('expected_google_account') != CECILIA or
                    settings.get('scopes') != [DRIVE_SCOPE]):
                raise DriveAuthError('DRIVE_AUTH_CONFIG_INVALID', stage='OAUTH_FLOW_CREATED')
            client_path = (self.root / settings['oauth_client_file']).resolve()
            store_path = (self.root / settings['credential_store']).resolve()
            if not client_path.is_relative_to(self.root.resolve()) or not store_path.is_relative_to(self.root.resolve()):
                raise DriveAuthError('DRIVE_AUTH_CONFIG_INVALID', stage='OAUTH_FLOW_CREATED')
            client = json.loads(client_path.read_text(encoding='utf-8'))
            installed = client.get('installed', {})
            if (not installed or installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth'
                    or installed.get('token_uri') != 'https://oauth2.googleapis.com/token'):
                raise DriveAuthError('DRIVE_AUTH_CLIENT_INVALID', stage='OAUTH_FLOW_CREATED')
            flow = self.flow_factory(client, DRIVE_SCOPE)
            credentials = self.runner(
                flow, event_sink=self._event, host='localhost', bind_addr='127.0.0.1', port=0,
                open_browser=True, timeout_seconds=self.timeout_seconds,
                authorization_prompt_message=None,
                success_message='Autorizacao recebida. Pode fechar esta janela.',
                login_hint=CECILIA, prompt='consent', include_granted_scopes='false',
                access_type='offline')
            previous = store_path.read_bytes() if store_path.exists() else None
            self.saver(credentials, store_path, CECILIA, event_sink=self._event)
            try:
                stored = self.store_factory(store_path).load()
                if not stored.get('refresh_token') or DRIVE_SCOPE not in stored.get('scopes', []):
                    raise DriveAuthError('DRIVE_CREDENTIAL_RELOAD_FAILED', stage='DPAPI_RELOAD')
            except Exception:
                self._restore_previous(store_path, previous)
                raise DriveAuthError('DRIVE_CREDENTIAL_RELOAD_FAILED', stage='DPAPI_RELOAD') from None
            try:
                self.poller.resume_after_reauth()
            except Exception:
                # Authentication succeeded; a local scheduler error must not trigger OAuth again.
                with self._guard:
                    self._error_code = 'POLLING_RESUME_FAILED'
                    self._error_stage = 'INCREMENTAL_CATCHUP'
            with self._guard:
                self._state = 'READY'
        except DriveAuthError as exc:
            with self._guard:
                self._state = 'AUTH_REQUIRED'
                self._error_code = exc.code
                self._error_stage = exc.stage
        except Exception:
            with self._guard:
                self._state = 'AUTH_REQUIRED'
                self._error_code = 'UNKNOWN_AUTH_FAILURE'
                self._error_stage = self._last_stage
