"""Read-only media identity guards. Observations and abandonment never grant reuse.

Callers that reserve/release media must hold BEGIN IMMEDIATE. At upload, the
same check must share the transaction that records the mutation intent. This
module performs no migration, lifecycle transition or remote operation.
"""

REMOTE_HISTORY_STEPS = (
    'UPLOAD_STARTED', 'UPLOAD', 'UPLOADED', 'TRANSFER_IN_PROGRESS',
    'TRANSFER_UNKNOWN', 'TRANSFER_CONFIRMED', 'TRANSFER_RECONCILIATION', 'ACCOUNT_SAFETY_GUARD',
    'UPLOAD_CONFIRMED', 'CAPTION', 'SAVE_DRAFT', 'TIKTOK_DRAFT_CONFIRMED',
    'TIKTOK_DRAFT_RECONCILED', 'TEST_FINAL_RESULT', 'PERSISTED',
    'REMOTE_DELETED', 'REMOTE_DELETE_PENDING', 'REMOTE_DELETE_CONFIRMED',
    'PERMANENT_DELETE_STARTED', 'MEDIA_CONSUMED',
)

MESSAGES = {
    'MEDIA_IDENTITY_UNPROVEN': 'Não foi possível comprovar a identidade da mídia. Revise o cadastro antes de novo uso.',
    'MEDIA_HISTORY_IDENTITY_UNPROVEN': 'A conta ou identidade do histórico não pôde ser comprovada. Revise o histórico; não envie novamente.',
    'MEDIA_HISTORY_RECONCILIATION_REQUIRED': 'Esta mídia tem possível envio anterior ao TikTok sem reconciliação. Revise o Draft e a conta indicados; não envie novamente.',
    'MEDIA_HISTORY_REUSE_NOT_AUTHORIZED': 'Esta mídia já possui persistência confirmada no TikTok. O histórico não autoriza outro upload.',
}


class MediaHistoryBlocked(ValueError):
    def __init__(self, blocker):
        self.blocker = blocker
        self.code = blocker['code']
        context = (f" Draft #{blocker['draft_id']} · Conta #{blocker['account_id']}."
                   if blocker.get('draft_id') is not None else '')
        super().__init__(f"{self.code}: {blocker['message']}{context}")


def _blocker(code, media, draft=None):
    return {'code': code, 'message': MESSAGES[code],
            'media_asset_id': media.get('id') if media else None,
            'draft_id': draft['id'] if draft else None,
            'account_id': draft['account_id'] if draft else None}


def canonical_media(conn, media):
    """Do not trust a caller's stale asset or a filename/checksum as identity."""
    if not media or not media.get('id'):
        return None
    row = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (media['id'],)).fetchone()
    if not row:
        return None
    stored = dict(row)
    for key in ('provider', 'source_account', 'remote_file_id'):
        value = stored.get(key)
        if not isinstance(value, str) or not value.strip() or value != value.strip() or media.get(key) != value:
            return None
    return stored


def same_media_drafts(conn, media):
    # Publisher account belongs to each historical attempt, not to the Drive
    # identity. Switching TikTok accounts must not erase risk for the same file.
    return [dict(row) for row in conn.execute(
        'SELECT d.* FROM publisher_drafts d JOIN publisher_media_assets m ON m.id=d.media_asset_id '
        'WHERE m.id=? OR (m.provider=? AND m.source_account=? AND m.remote_file_id=?) ORDER BY d.id',
        (media['id'], media['provider'], media['source_account'], media['remote_file_id']),
    )]


def remote_history(conn, draft):
    """Absence from a current Studio list is never proof of no historical effect."""
    placeholders = ','.join('?' for _ in REMOTE_HISTORY_STEPS)
    checkpoint = conn.execute(
        f'SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND UPPER(step) IN ({placeholders}) LIMIT 1',
        (draft['id'], *REMOTE_HISTORY_STEPS),
    ).fetchone()
    ledger = [dict(row) for row in conn.execute(
        'SELECT account_id FROM account_mutation_ledger WHERE draft_id=?', (draft['id'],),
    )]
    evidence = [dict(row) for row in conn.execute(
        'SELECT account_id,media_asset_id,outcome FROM publisher_reconciliation_evidence WHERE draft_id=?',
        (draft['id'],),
    )]
    identity_mismatch = any(row['account_id'] != draft['account_id'] for row in ledger) or any(
        (row['account_id'], row['media_asset_id']) != (draft['account_id'], draft['media_asset_id'])
        for row in evidence)
    effect = bool(checkpoint or ledger or evidence
                  or draft.get('upload_status') not in (None, 'NOT_STARTED')
                  or draft.get('processing_status') not in (None, 'NOT_STARTED')
                  or draft.get('persistence_status') == 'PERSISTED'
                  or any(draft.get(key) for key in (
                      'confirmed_at', 'upload_confirmed_at', 'caption_applied_at',
                      'tiktok_draft_persisted_at', 'tiktok_draft_id', 'tiktok_draft_url')))
    # Successful domain reconciliation is durable, unlike a listing observation.
    # It permits existing review/consumption, not a new identity's second upload.
    from src.publisher.persistence_identity import persistence_identity_verified
    persisted = (draft.get('persistence_status') == 'PERSISTED' and bool(draft.get('confirmed_at'))
                 and persistence_identity_verified(conn,draft))
    return {'effect': effect, 'identity_mismatch': identity_mismatch, 'persisted': persisted}


def past_execution_proven_local(conn, draft):
    items = conn.execute(
        'SELECT i.status,i.error_code,e.status execution_status FROM publisher_execution_items i '
        'JOIN publisher_batch_executions e ON e.id=i.execution_id WHERE i.draft_id=?', (draft['id'],),
    ).fetchall()
    if not items:
        return True
    # An archive flag alone is not local proof. Use the existing audited safe
    # cancellation contract; never infer safety from reset/pristine flags.
    cancelled = conn.execute(
        "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step='BATCH_CANCELLED' AND status='OK' LIMIT 1",
        (draft['id'],),
    ).fetchone()
    return bool(cancelled) and all(
        item['status'] == 'FAILED' and item['error_code'] == 'LOCAL_BATCH_CANCELLED'
        and item['execution_status'] in ('PAUSED', 'COMPLETED') for item in items)


def media_history_blocker(conn, media, *, draft_id=None, account_id=None, purpose='upload'):
    if purpose not in ('reserve', 'upload', 'release', 'consume'):
        raise ValueError('INVALID_MEDIA_HISTORY_PURPOSE')
    stored = canonical_media(conn, media)
    if not stored:
        return _blocker('MEDIA_IDENTITY_UNPROVEN', media)
    if purpose == 'consume' and (draft_id is None or account_id is None):
        return _blocker('MEDIA_HISTORY_IDENTITY_UNPROVEN', stored)
    if account_id is not None and not conn.execute(
            'SELECT 1 FROM publisher_accounts WHERE id=?', (account_id,)).fetchone():
        return _blocker('MEDIA_HISTORY_IDENTITY_UNPROVEN', stored)
    if draft_id is not None:
        current = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (draft_id,)).fetchone()
        if not current or current['media_asset_id'] != stored['id'] or (
                account_id is not None and current['account_id'] != account_id):
            return _blocker('MEDIA_HISTORY_IDENTITY_UNPROVEN', stored)
    # Consumption/recovery of the existing identity is not permission to reuse
    # it. Persisted history and a delete checkpoint still prohibit new uploads.
    if purpose in ('reserve', 'upload') and (stored.get('consumed_by_draft_id') is not None or stored.get('remote_deleted_at')):
        return _blocker('MEDIA_HISTORY_REUSE_NOT_AUTHORIZED', stored)
    for draft in same_media_drafts(conn, stored):
        history = remote_history(conn, draft)
        if history['identity_mismatch'] or not conn.execute(
                'SELECT 1 FROM publisher_accounts WHERE id=?', (draft['account_id'],)).fetchone():
            return _blocker('MEDIA_HISTORY_IDENTITY_UNPROVEN', stored, draft)
        if history['effect'] and not history['persisted']:
            return _blocker('MEDIA_HISTORY_RECONCILIATION_REQUIRED', stored, draft)
        if draft['id'] != draft_id and not history['effect'] and not past_execution_proven_local(conn, draft):
            return _blocker('MEDIA_HISTORY_RECONCILIATION_REQUIRED', stored, draft)
        if history['persisted'] and draft['id'] != draft_id and purpose in ('reserve', 'upload'):
            return _blocker('MEDIA_HISTORY_REUSE_NOT_AUTHORIZED', stored, draft)
    return None


def assert_media_history_allowed(conn, media, **context):
    blocker = media_history_blocker(conn, media, **context)
    if blocker:
        raise MediaHistoryBlocked(blocker)
