"""Explicit, identity-bound Drive deletion consent in the existing audit ledger."""
import hashlib
import json
from contextlib import nullcontext
from src.store import now_iso

STEP = 'DRIVE_CLEANUP_AUTHORIZED'
OPERATION = 'DRIVE_MEDIA_CONSUMPTION'

class CleanupAuthorizationBlocked(ValueError):
    def __init__(self, code):
        self.code = code
        super().__init__(code)

def cleanup_identity(conn, draft_id):
    draft = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (draft_id,)).fetchone()
    if not draft:
        raise CleanupAuthorizationBlocked('CLEANUP_IDENTITY_CHANGED')
    media = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (draft['media_asset_id'],)).fetchone()
    if not media:
        raise CleanupAuthorizationBlocked('CLEANUP_IDENTITY_CHANGED')
    return dict(operation=OPERATION, draft_id=draft['id'], account_id=draft['account_id'],
                media_asset_id=media['id'], batch_id=draft['batch_id'],
                **{key: media[key] for key in ('provider','source_account','remote_file_id',
                   'remote_folder_id','filename','mime_type','size_bytes','checksum','remote_modified_at')})

def cleanup_identity_token(conn, draft_id):
    value = json.dumps(cleanup_identity(conn, draft_id), sort_keys=True, separators=(',', ':'))
    return hashlib.sha256(value.encode()).hexdigest()

def authorize_cleanup(conn, draft_id, *, human_confirmed, identity_token, request_id=None, identity_validator=None):
    if human_confirmed is not True:
        raise CleanupAuthorizationBlocked('EXPLICIT_DRIVE_DELETION_AUTHORIZATION_REQUIRED')
    owns = not conn.in_transaction
    with conn if owns else nullcontext():
        if owns: conn.execute('BEGIN IMMEDIATE')
        if identity_validator:
            identity_validator()
        from src.publisher.lifecycle import assert_cleanup_allowed
        assert_cleanup_allowed(conn,draft_id)
        identity = cleanup_identity(conn, draft_id)
        if identity_token != cleanup_identity_token(conn, draft_id):
            raise CleanupAuthorizationBlocked('CLEANUP_IDENTITY_CHANGED')
        payload = dict(identity=identity, human_confirmed=True, confirmed_count=1, request_id=request_id)
        cursor = conn.execute('INSERT INTO publisher_execution_logs(draft_id,step,status,message,created_at) '
                              'VALUES (?,?,?,?,?)', (draft_id,STEP,'AUTHORIZED',json.dumps(payload,sort_keys=True),now_iso()))
        return cursor.lastrowid

def assert_cleanup_authorized(conn, draft_id, authorization_id):
    if type(authorization_id) is not int or authorization_id <= 0:
        raise CleanupAuthorizationBlocked('EXPLICIT_DRIVE_DELETION_AUTHORIZATION_REQUIRED')
    row = conn.execute('SELECT * FROM publisher_execution_logs WHERE id=? AND draft_id=? AND step=? AND status=?',
                       (authorization_id,draft_id,STEP,'AUTHORIZED')).fetchone()
    if not row:
        raise CleanupAuthorizationBlocked('CLEANUP_AUTHORIZATION_NOT_FOUND')
    try: payload = json.loads(row['message'])
    except (TypeError,ValueError): raise CleanupAuthorizationBlocked('CLEANUP_AUTHORIZATION_INVALID') from None
    if (not isinstance(payload, dict) or payload.get('human_confirmed') is not True
            or type(payload.get('confirmed_count')) is not int or payload.get('confirmed_count') != 1
            or payload.get('identity') != cleanup_identity(conn,draft_id)):
        raise CleanupAuthorizationBlocked('CLEANUP_IDENTITY_CHANGED')
    return payload
