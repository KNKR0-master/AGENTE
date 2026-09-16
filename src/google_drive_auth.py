"""Drive authentication only; never initiates consent or falls back identities."""
import json
import os
import tempfile
from pathlib import Path

from src.tiktok_shop_api import TokenCipher

DRIVE_SCOPE = 'https://www.googleapis.com/auth/drive'
CECILIA = 'ceciliafigueiredo.shop@gmail.com'


class DriveAuthError(ValueError):
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


def validate_account(service, expected):
    try:
        user = service.about().get(fields='user(emailAddress,me)').execute().get('user', {})
    except Exception:
        raise DriveAuthError('GOOGLE_ACCOUNT_MISMATCH') from None
    email = str(user.get('emailAddress') or '').strip().lower()
    if not expected or not user.get('me') or email != expected.strip().lower():
        raise DriveAuthError('GOOGLE_ACCOUNT_MISMATCH')
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
    # Only verified user credentials may replace the encrypted local store.
    store.save(json.loads(credentials.to_json()))
    return service


def save_authorized_user(credentials, store_path, expected=CECILIA, *, builder=None, store=None):
    """For a future explicit consent flow; this function never opens a browser."""
    from googleapiclient.discovery import build
    granted = credentials.granted_scopes
    if (not credentials.has_scopes([DRIVE_SCOPE]) or not credentials.refresh_token or
            (granted is not None and DRIVE_SCOPE not in granted)):
        raise DriveAuthError('GOOGLE_OAUTH_SCOPE_OR_REFRESH_TOKEN_MISSING')
    service = (builder or build)('drive', 'v3', credentials=credentials, cache_discovery=False)
    validate_account(service, expected)
    (store or DriveCredentialStore(store_path)).save(json.loads(credentials.to_json()))
    return service
