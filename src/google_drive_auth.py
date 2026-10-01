"""Drive authentication only; never initiates consent or falls back identities."""
import json
import os
import tempfile
from threading import Lock
from pathlib import Path

from src.tiktok_shop_api import TokenCipher

DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
CECILIA = 'ceciliafigueiredo.shop@gmail.com'


class DriveAuthError(ValueError):
    def __init__(self, message, *, code=None, stage=None):
        super().__init__(message)
        self.code = code or message
        self.stage = stage


class SafeAuthCause(Exception):
    def __init__(self, exception_type, *, http_status=None, reason=None, winerror=None):
        self.exception_type = exception_type
        self.http_status = http_status
        self.reason = reason
        self.winerror = winerror
        super().__init__(f"{exception_type}:{http_status or 'unknown'}:{reason or 'unspecified'}")


def safe_auth_cause(exc):
    response = getattr(exc, 'resp', None) or getattr(exc, 'response', None)
    status = getattr(response, 'status', None) or getattr(response, 'status_code', None)
    try:
        status = int(status) if status is not None else None
    except (TypeError, ValueError):
        status = None
    allowed_reasons = {'invalid_grant', 'insufficientPermissions', 'authError', 'forbidden',
                       'rateLimitExceeded', 'userRateLimitExceeded'}
    reason = getattr(exc, 'error', None)
    reason = reason if isinstance(reason, str) and reason in allowed_reasons else None
    return SafeAuthCause(type(exc).__name__, http_status=status, reason=reason,
                         winerror=getattr(exc, 'winerror', None))


def _auth_event(sink, stage, **fields):
    if sink is not None:
        try:
            sink({'stage': stage, **fields})
        except Exception:
            pass


class DriveCredentialStore:
    def __init__(self, path, cipher=None):
        self.path = Path(path)
        self.cipher = cipher or TokenCipher()

    def load(self):
        if not self.path.is_file():
            raise DriveAuthError('GOOGLE_USER_CONSENT_REQUIRED')
        try:
            return json.loads(self.cipher.decrypt(self.path.read_text(encoding='utf-8')))
        except Exception:
            raise DriveAuthError('GOOGLE_CREDENTIAL_STORE_UNREADABLE') from None

    def diagnostic(self):
        """Return local credential metadata without exposing decrypted secrets."""
        result = {
            'file_exists': False,
            'file_readable': False,
            'dpapi_load_ok': False,
            'json_valid': False,
            'exception_type': None,
            'winerror': None,
        }
        try:
            result['file_exists'] = self.path.is_file()
            if not result['file_exists']:
                return result
            ciphertext = self.path.read_text(encoding='utf-8')
            result['file_readable'] = True
            plaintext = self.cipher.decrypt(ciphertext)
            payload = json.loads(plaintext)
            if not isinstance(payload, dict):
                raise ValueError('credential payload is not an object')
            result['dpapi_load_ok'] = True
            result['json_valid'] = True
            result['has_refresh_token'] = bool(payload.get('refresh_token'))
            result['scopes'] = [str(scope) for scope in payload.get('scopes', [])]
        except OSError as exc:
            result.update(
                exception_type=type(exc).__name__,
                winerror=getattr(exc, 'winerror', None),
            )
        except (ValueError, TypeError, UnicodeError) as exc:
            result['exception_type'] = type(exc).__name__
        return result

    def save(self, info):
        encrypted = self.cipher.encrypt(json.dumps(info))
        self.path.parent.mkdir(parents=True, exist_ok=True)
        fd, temporary = tempfile.mkstemp(prefix='.credential-', dir=self.path.parent)
        try:
            with os.fdopen(fd, 'w', encoding='utf-8') as stream:
                stream.write(encrypted)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            Path(temporary).unlink(missing_ok=True)


def validate_account(service, expected, *, event_sink=None):
    _auth_event(event_sink, 'ACCOUNT_VALIDATION_STARTED')
    try:
        user = service.about().get(fields='user(emailAddress,me)').execute().get('user', {})
    except Exception as exc:
        cause = safe_auth_cause(exc)
        _auth_event(event_sink, 'ACCOUNT_VALIDATION_FAILED', exception_type=cause.exception_type,
                    http_status=cause.http_status, reason=cause.reason)
        raise DriveAuthError('GOOGLE_ACCOUNT_MISMATCH', code='ACCOUNT_VALIDATION_FAILED',
                             stage='ACCOUNT_VALIDATION') from cause
    _auth_event(event_sink, 'ACCOUNT_VALIDATION_REQUEST_COMPLETED', identity_observed=bool(user.get('emailAddress')))
    email = str(user.get('emailAddress') or '').strip().lower()
    if not expected or not user.get('me') or email != expected.strip().lower():
        _auth_event(event_sink, 'ACCOUNT_VALIDATION_COMPLETED', account_match=False)
        raise DriveAuthError('GOOGLE_ACCOUNT_MISMATCH', code='ACCOUNT_MISMATCH',
                             stage='ACCOUNT_VALIDATION')
    _auth_event(event_sink, 'ACCOUNT_VALIDATION_COMPLETED', account_match=True)
    return email


def user_drive_service(store_path, expected, *, store=None, credential_factory=None,
                       builder=None, request_factory=None):
    from google.oauth2.credentials import Credentials
    from google.auth.transport.requests import Request
    from googleapiclient.discovery import build
    store = store or DriveCredentialStore(store_path)
    info = store.load()
    if not info.get('refresh_token'):
        raise DriveAuthError('GOOGLE_USER_CONSENT_REQUIRED')
    if DRIVE_SCOPE not in info.get('scopes', []):
        raise DriveAuthError('GOOGLE_OAUTH_SCOPE_INSUFFICIENT')
    # Validate endpoints before allowing refresh with a stored client secret.
    if info.get('token_uri', 'https://oauth2.googleapis.com/token') != 'https://oauth2.googleapis.com/token':
        raise DriveAuthError('GOOGLE_CREDENTIAL_ENDPOINT_INVALID')
    factory = credential_factory or Credentials.from_authorized_user_info
    try:
        credentials = factory(info)
        if not credentials.valid:
            credentials.refresh((request_factory or Request)())
        granted = credentials.granted_scopes
        if granted is not None and DRIVE_SCOPE not in granted:
            raise DriveAuthError('GOOGLE_OAUTH_SCOPE_INSUFFICIENT')
        service = (builder or build)('drive', 'v3', credentials=credentials, cache_discovery=False)
    except DriveAuthError:
        raise
    except Exception:
        raise DriveAuthError('GOOGLE_USER_CREDENTIAL_REFRESH_FAILED') from None
    validate_account(service, expected)

    # Access-token/expiry changes stay in memory. Only a rotated refresh token
    # needs to survive a restart, and only after account validation.
    persisted_refresh_token = info['refresh_token']
    refresh_lock = Lock()

    def persist_rotated_refresh_token():
        nonlocal persisted_refresh_token
        with refresh_lock:
            current = credentials.refresh_token
            if current and current != persisted_refresh_token:
                store.save(json.loads(credentials.to_json()))
                persisted_refresh_token = current

    persist_rotated_refresh_token()
    original_refresh = credentials.refresh

    def refresh_and_persist(request):
        original_refresh(request)
        persist_rotated_refresh_token()

    credentials.refresh = refresh_and_persist
    return service


def save_authorized_user(credentials, store_path, expected=CECILIA, *, builder=None, store=None,
                         event_sink=None):
    """For a future explicit consent flow; this function never opens a browser."""
    from googleapiclient.discovery import build
    _auth_event(event_sink, 'SAVE_AUTHORIZED_USER_STARTED')
    granted = credentials.granted_scopes
    refresh_present = bool(credentials.refresh_token)
    _auth_event(event_sink, 'REFRESH_TOKEN_CHECK', refresh_token_present=refresh_present)
    if not refresh_present:
        raise DriveAuthError('GOOGLE_OAUTH_SCOPE_OR_REFRESH_TOKEN_MISSING',
                             code='REFRESH_TOKEN_MISSING', stage='REFRESH_TOKEN_CHECK')
    if not credentials.has_scopes([DRIVE_SCOPE]) or (granted is not None and DRIVE_SCOPE not in granted):
        raise DriveAuthError('GOOGLE_OAUTH_SCOPE_OR_REFRESH_TOKEN_MISSING',
                             code='GOOGLE_OAUTH_SCOPE_OR_REFRESH_TOKEN_MISSING', stage='REFRESH_TOKEN_CHECK')
    service = (builder or build)('drive', 'v3', credentials=credentials, cache_discovery=False)
    validate_account(service, expected, event_sink=event_sink)
    _auth_event(event_sink, 'DPAPI_SAVE_STARTED')
    try:
        (store or DriveCredentialStore(store_path)).save(json.loads(credentials.to_json()))
    except Exception as exc:
        cause = safe_auth_cause(exc)
        _auth_event(event_sink, 'DPAPI_SAVE_FAILED', exception_type=cause.exception_type,
                    winerror=cause.winerror)
        raise DriveAuthError('DPAPI_SAVE_FAILED', code='DPAPI_SAVE_FAILED', stage='DPAPI_SAVE') from cause
    _auth_event(event_sink, 'DPAPI_SAVE_COMPLETED')
    _auth_event(event_sink, 'AUTH_COMPLETED')
    return service
