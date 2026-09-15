"""Explicit single-use Desktop consent; identity, DPAPI and metadata only."""
import json
import logging
import sqlite3
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from src.google_drive_auth import (CECILIA, DRIVE_SCOPE, DriveAuthError,
                                   DriveCredentialStore, save_authorized_user)

REMOTE = '1A72hhOtiz0I6DZOz4BIpAjnjKkYCZ4uX'
LOCK = ROOT / 'data/credentials/cecilia-user-consent-20260913.consumed'


def draft_snapshot():
    with sqlite3.connect((ROOT / 'data/agent.db').as_uri() + '?mode=ro', uri=True) as conn:
        conn.row_factory = sqlite3.Row
        draft = dict(conn.execute('SELECT * FROM publisher_drafts WHERE id=1').fetchone())
        asset = dict(conn.execute('SELECT * FROM publisher_media_assets WHERE id=?',
                                  (draft['media_asset_id'],)).fetchone())
    if (draft['status'] != 'READY_FOR_REVIEW' or draft['persistence_status'] != 'PERSISTED'
            or asset['status'] != 'CONSUMED_PENDING_DELETE'
            or asset['remote_file_id'] != REMOTE):
        raise DriveAuthError('DRAFT_PRECONDITION_FAILED')
    temp = Path(draft['temp_path'])
    if not temp.is_file():
        raise DriveAuthError('TEMP_MISSING')
    return draft, asset, (str(temp), temp.stat().st_size, temp.stat().st_mtime_ns)


def main():
    from google_auth_oauthlib.flow import InstalledAppFlow

    logging.disable(logging.CRITICAL)
    config = json.loads((ROOT / 'config/cecilia_drive_auth.json').read_text())
    if (config['auth_mode'] != 'user_oauth' or config['expected_google_account'] != CECILIA
            or config['scopes'] != [DRIVE_SCOPE]):
        raise DriveAuthError('CONFIG_MISMATCH')
    client = json.loads((ROOT / config['oauth_client_file']).read_text())
    installed = client.get('installed', {})
    if (installed.get('project_id') != 'banded-chimera-505001-g3'
            or installed.get('auth_uri') != 'https://accounts.google.com/o/oauth2/auth'
            or installed.get('token_uri') != 'https://oauth2.googleapis.com/token'):
        raise DriveAuthError('CLIENT_CONFIG_INVALID')
    store_path = ROOT / config['credential_store']
    if store_path.exists():
        raise DriveAuthError('CREDENTIAL_ALREADY_EXISTS')
    before = draft_snapshot()
    flow = InstalledAppFlow.from_client_config(client, scopes=[DRIVE_SCOPE],
                                               autogenerate_code_verifier=True)
    # Never remove this marker automatically, including after failure or timeout.
    with LOCK.open('x', encoding='utf-8') as marker:
        marker.write('SINGLE_AUTHORIZATION_CONSUMED\n')
    print('SINGLE_OAUTH_STARTED=true', flush=True)
    credentials = flow.run_local_server(
        host='localhost', bind_addr='127.0.0.1', port=0,
        open_browser=True, timeout_seconds=600,
        authorization_prompt_message=None,
        success_message='Callback recebido. Pode fechar esta janela. A identidade sera validada pelo Publisher.',
        login_hint=CECILIA, prompt='consent', include_granted_scopes='false',
        access_type='offline')
    if set(credentials.granted_scopes or credentials.scopes or []) != {DRIVE_SCOPE}:
        raise DriveAuthError('GOOGLE_OAUTH_SCOPE_MISMATCH')
    service = save_authorized_user(credentials, store_path, CECILIA)
    stored = DriveCredentialStore(store_path).load()
    valid = bool(stored.get('refresh_token') and stored.get('token')
                 and store_path.read_text().startswith('dpapi:v1:'))
    print('OAUTH_USER_AUTHENTICATED=true', flush=True)
    print('GOOGLE_ACCOUNT=' + CECILIA, flush=True)
    print('USER_OAUTH_CREDENTIAL_STORED=true', flush=True)
    print('DPAPI_STORAGE_VALID=' + str(valid).lower(), flush=True)
    metadata = service.files().get(fileId=REMOTE, supportsAllDrives=True,
        fields='id,ownedByMe,trashed,capabilities(canDelete,canTrash)').execute(num_retries=0)
    result = {
        'EXPECTED_GOOGLE_ACCOUNT': CECILIA, 'CECILIA_DRIVE_AUTH_MODE': config['auth_mode'],
        'REMOTE_FILE_ID': REMOTE, 'OWNED_BY_ME': metadata.get('ownedByMe'),
        'CAN_DELETE': metadata.get('capabilities', {}).get('canDelete'),
        'CAN_TRASH': metadata.get('capabilities', {}).get('canTrash'),
        'TRASHED': metadata.get('trashed'),
        'DRAFT_1_STATE_PRESERVED': before == draft_snapshot(),
        'REMOTE_FILE_DELETED': False, 'TEMP_FILE_DELETED': False,
    }
    print(json.dumps(result), flush=True)


if __name__ == '__main__':
    try:
        main()
    except FileExistsError:
        print('OAUTH_RESULT=AUTHORIZATION_ALREADY_CONSUMED', flush=True)
        sys.exit(1)
    except DriveAuthError as exc:
        print('OAUTH_RESULT=' + str(exc), flush=True)
        sys.exit(1)
    except Exception:
        # OAuth exceptions may contain callback codes or token response bodies.
        print('OAUTH_RESULT=FAILED_NO_RETRY', flush=True)
        sys.exit(1)
