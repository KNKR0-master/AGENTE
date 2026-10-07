"""Append-only observations; recording one never changes a draft or permits retry."""

import json
from datetime import datetime, timezone

from src.store import now_iso


OUTCOMES = frozenset({
    'REMOTE_EXISTS_CONFIRMED', 'REMOTE_ABSENCE_CONFIRMED',
    'REMOTE_MATCH_AMBIGUOUS', 'REMOTE_STATE_UNKNOWN',
})
COVERAGES = frozenset({'COMPLETE', 'PARTIAL', 'UNKNOWN'})
BLOCKERS = frozenset({
    'UNSAVED_EDITOR', 'AUTH_REQUIRED', 'CAPTCHA', 'TWO_FACTOR',
    'SECURITY_CHALLENGE', 'WRONG_ACCOUNT', 'PARTIAL_LISTING',
    'LISTING_ERROR', 'MATCH_EVIDENCE_INSUFFICIENT',
})
REMOTE_EFFECT_STEPS = ('UPLOAD_STARTED', 'SAVE_DRAFT', 'TIKTOK_DRAFT_CONFIRMED')
SOURCES = frozenset({'TIKTOK_STUDIO_DRAFT_LIST', 'TIKTOK_STUDIO_EDITOR', 'TIKTOK_STUDIO_AUTH'})
PAYLOAD_KEYS = frozenset({
    'candidate_summaries', 'observed_duration', 'caption_fingerprint',
    'thumbnail_fingerprint', 'listing_bounds', 'pagination_observations',
    'current_listing', 'page_identity',
})


def _instant(value):
    try:
        parsed = datetime.fromisoformat(str(value).replace('Z', '+00:00'))
    except (TypeError, ValueError) as exc:
        raise ValueError('INVALID_EVIDENCE_TIMESTAMP') from exc
    if parsed.tzinfo is None:
        raise ValueError('EVIDENCE_TIMESTAMP_REQUIRES_TIMEZONE')
    return parsed.astimezone(timezone.utc)


def _latest_remote_checkpoint(conn, draft_id):
    return conn.execute(
        'SELECT id,created_at FROM publisher_execution_logs WHERE draft_id=? '
        'AND step IN (?,?,?) ORDER BY id DESC LIMIT 1',
        (draft_id, *REMOTE_EFFECT_STEPS),
    ).fetchone()


def _safe_payload(payload):
    if not isinstance(payload, dict) or set(payload) - PAYLOAD_KEYS:
        raise ValueError('UNSAFE_EVIDENCE_PAYLOAD')

    def check(value):
        if isinstance(value, dict):
            for key, child in value.items():
                if not isinstance(key, str) or any(
                    word in key.casefold() for word in ('token', 'cookie', 'secret', 'password', 'credential', 'session')
                ):
                    raise ValueError('UNSAFE_EVIDENCE_PAYLOAD')
                check(child)
        elif isinstance(value, list):
            for child in value:
                check(child)
        elif value is not None and not isinstance(value, (str, int, float, bool)):
            raise ValueError('UNSAFE_EVIDENCE_PAYLOAD')

    check(payload)
    return json.dumps(payload, ensure_ascii=True, sort_keys=True, allow_nan=False)


def record_reconciliation_evidence(conn, *, draft_id, media_asset_id, account_id,
                                   expected_account, observed_account, outcome,
                                   coverage_status, source_surface, candidate_count,
                                   matcher_version, remote_effect_checkpoint_id,
                                   verification_started_at, verification_completed_at,
                                   execution_id=None, blocker_code=None,
                                   matched_candidate_fingerprint=None, valid_until=None,
                                   evidence_payload=None):
    """Record a completed inspection against the current remote-effect checkpoint."""
    if conn.in_transaction:
        raise ValueError('EVIDENCE_RECORD_REQUIRES_OWN_TRANSACTION')
    if outcome not in OUTCOMES or coverage_status not in COVERAGES:
        raise ValueError('INVALID_EVIDENCE_CLASSIFICATION')
    if blocker_code is not None and blocker_code not in BLOCKERS:
        raise ValueError('INVALID_EVIDENCE_BLOCKER')
    if source_surface not in SOURCES or not str(matcher_version or '').strip():
        raise ValueError('INVALID_EVIDENCE_SOURCE')
    if not isinstance(candidate_count, int) or isinstance(candidate_count, bool) or candidate_count < 0:
        raise ValueError('INVALID_CANDIDATE_COUNT')
    if outcome == 'REMOTE_ABSENCE_CONFIRMED' and (coverage_status != 'COMPLETE' or candidate_count != 0):
        raise ValueError('ABSENCE_REQUIRES_COMPLETE_ZERO_CANDIDATES')
    if outcome == 'REMOTE_EXISTS_CONFIRMED' and (
        coverage_status != 'COMPLETE' or candidate_count != 1 or not matched_candidate_fingerprint
    ):
        raise ValueError('EXISTENCE_REQUIRES_UNIQUE_STRONG_MATCH')
    if outcome == 'REMOTE_MATCH_AMBIGUOUS' and candidate_count < 2:
        raise ValueError('AMBIGUITY_REQUIRES_MULTIPLE_CANDIDATES')
    if outcome != 'REMOTE_EXISTS_CONFIRMED' and matched_candidate_fingerprint is not None:
        raise ValueError('FINGERPRINT_REQUIRES_CONFIRMED_MATCH')
    if not str(expected_account or '').strip():
        raise ValueError('EXPECTED_ACCOUNT_REQUIRED')
    if outcome in {'REMOTE_EXISTS_CONFIRMED', 'REMOTE_ABSENCE_CONFIRMED'} and observed_account != expected_account:
        raise ValueError('ACCOUNT_IDENTITY_NOT_CONFIRMED')
    started, completed = _instant(verification_started_at), _instant(verification_completed_at)
    if completed < started:
        raise ValueError('EVIDENCE_TIME_ORDER_INVALID')
    if valid_until is not None and _instant(valid_until) <= completed:
        raise ValueError('EVIDENCE_EXPIRY_INVALID')
    payload_json = _safe_payload(evidence_payload if evidence_payload is not None else {})

    conn.execute('BEGIN IMMEDIATE')
    try:
        draft = conn.execute('SELECT media_asset_id,account_id,status,persistence_status FROM publisher_drafts WHERE id=?', (draft_id,)).fetchone()
        media = conn.execute('SELECT status FROM publisher_media_assets WHERE id=?', (media_asset_id,)).fetchone()
        account = conn.execute('SELECT tiktok_username FROM publisher_accounts WHERE id=?', (account_id,)).fetchone()
        if not draft or draft['media_asset_id'] != media_asset_id or draft['account_id'] != account_id or not account:
            raise ValueError('EVIDENCE_IDENTITY_MISMATCH')
        if not media or draft['status'] not in {'MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED'} or (
            draft['persistence_status'] != 'UNCONFIRMED' or media['status'] != 'NEEDS_RECONCILIATION'
        ):
            raise ValueError('EVIDENCE_LIFECYCLE_CHANGED')
        if account['tiktok_username'] and account['tiktok_username'] != expected_account:
            raise ValueError('EXPECTED_ACCOUNT_MISMATCH')
        checkpoint = _latest_remote_checkpoint(conn, draft_id)
        if not checkpoint or checkpoint['id'] != remote_effect_checkpoint_id:
            raise ValueError('REMOTE_EFFECT_CHECKPOINT_MISMATCH')
        if started < _instant(checkpoint['created_at']):
            raise ValueError('VERIFICATION_PRECEDES_REMOTE_EFFECT')
        if execution_id is not None:
            item = conn.execute(
                'SELECT e.started_at FROM publisher_execution_items i '
                'JOIN publisher_batch_executions e ON e.id=i.execution_id '
                'WHERE i.execution_id=? AND i.draft_id=?', (execution_id, draft_id),
            ).fetchone()
            latest_item = conn.execute(
                'SELECT execution_id FROM publisher_execution_items WHERE draft_id=? '
                'ORDER BY execution_id DESC LIMIT 1', (draft_id,),
            ).fetchone()
            if not item or not latest_item or latest_item['execution_id'] != execution_id or (
                _instant(checkpoint['created_at']) < _instant(item['started_at'])
            ):
                raise ValueError('EVIDENCE_EXECUTION_ITEM_MISMATCH')
        cursor = conn.execute(
            'INSERT INTO publisher_reconciliation_evidence ('
            'draft_id,media_asset_id,execution_id,execution_item_draft_id,account_id,'
            'expected_account,observed_account,outcome,coverage_status,blocker_code,'
            'source_surface,candidate_count,matched_candidate_fingerprint,matcher_version,'
            'remote_effect_checkpoint_id,verification_started_at,verification_completed_at,'
            'valid_until,evidence_payload_json) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)',
            (draft_id, media_asset_id, execution_id, draft_id if execution_id is not None else None,
             account_id, expected_account, observed_account, outcome, coverage_status,
             blocker_code, source_surface, candidate_count, matched_candidate_fingerprint,
             matcher_version, remote_effect_checkpoint_id, verification_started_at,
             verification_completed_at, valid_until, payload_json),
        )
        evidence_id = cursor.lastrowid
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    return evidence_id


def get_latest_reconciliation_evidence(conn, *, draft_id, execution_id=None):
    query = 'SELECT * FROM publisher_reconciliation_evidence WHERE draft_id=?'
    params = [draft_id]
    if execution_id is not None:
        query += ' AND execution_id=? AND execution_item_draft_id=?'
        params.extend((execution_id, draft_id))
    row = conn.execute(query + ' ORDER BY id DESC LIMIT 1', params).fetchone()
    return dict(row) if row else None


def evaluate_reconciliation_evidence_validity(conn, evidence_id, *, draft_id,
                                              media_asset_id, account_id,
                                              execution_id=None, now=None):
    """Check binding and freshness; this function grants no mutation capability."""
    row = conn.execute('SELECT * FROM publisher_reconciliation_evidence WHERE id=?', (evidence_id,)).fetchone()
    if not row:
        return {'valid': False, 'reason': 'EVIDENCE_NOT_FOUND'}
    if (row['draft_id'], row['media_asset_id'], row['account_id']) != (draft_id, media_asset_id, account_id):
        return {'valid': False, 'reason': 'EVIDENCE_IDENTITY_MISMATCH'}
    if execution_id is not None and (row['execution_id'], row['execution_item_draft_id']) != (execution_id, draft_id):
        return {'valid': False, 'reason': 'EVIDENCE_EXECUTION_ITEM_MISMATCH'}
    if row['execution_id'] is not None:
        latest_item = conn.execute(
            'SELECT execution_id FROM publisher_execution_items WHERE draft_id=? '
            'ORDER BY execution_id DESC LIMIT 1', (draft_id,),
        ).fetchone()
        if not latest_item or latest_item['execution_id'] != row['execution_id']:
            return {'valid': False, 'reason': 'EVIDENCE_EXECUTION_ITEM_STALE'}
    draft = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (draft_id,)).fetchone()
    media = conn.execute('SELECT status FROM publisher_media_assets WHERE id=?', (media_asset_id,)).fetchone()
    account = conn.execute('SELECT tiktok_username FROM publisher_accounts WHERE id=?', (account_id,)).fetchone()
    if not draft or not media or not account or draft['media_asset_id'] != media_asset_id or draft['account_id'] != account_id:
        return {'valid': False, 'reason': 'CURRENT_IDENTITY_CHANGED'}
    if account['tiktok_username'] and account['tiktok_username'] != row['expected_account']:
        return {'valid': False, 'reason': 'ACCOUNT_IDENTITY_CHANGED'}
    if draft['status'] not in {'MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED'} or (
        draft['persistence_status'] != 'UNCONFIRMED' or media['status'] != 'NEEDS_RECONCILIATION'
    ):
        return {'valid': False, 'reason': 'LIFECYCLE_CHANGED'}
    checkpoint = _latest_remote_checkpoint(conn, draft_id)
    if not checkpoint or checkpoint['id'] != row['remote_effect_checkpoint_id']:
        return {'valid': False, 'reason': 'REMOTE_EFFECT_CHECKPOINT_STALE'}
    if row['valid_until'] is None or _instant(row['valid_until']) <= _instant(now or now_iso()):
        return {'valid': False, 'reason': 'EVIDENCE_EXPIRED_OR_UNBOUNDED'}
    return {'valid': True, 'reason': 'CURRENT'}
