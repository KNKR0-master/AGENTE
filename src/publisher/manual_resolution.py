"""Explicit local decisions about an already ambiguous upload; no remote client."""

import json

from src.publisher.lifecycle import reconcile_received
from src.publisher.queue_policy import reconciliation_context
from src.publisher.media_history import media_history_blocker
from src.publisher.reconciliation_evidence import get_latest_reconciliation_evidence
from src.publisher.service import get_row, log
from src.store import now_iso


def resolve_draft(conn, draft_id, data):
    decision = data.get('decision')
    if decision not in {'CONFIRM_EXISTS', 'ABANDON', 'UNKNOWN'}:
        raise ValueError('INVALID_RESOLUTION_DECISION')
    if data.get('human_confirmed') is not True:
        raise ValueError('EXPLICIT_RESOLUTION_CONFIRMATION_REQUIRED')
    reason = str(data.get('reason') or '').strip()
    if not 5 <= len(reason) <= 1000:
        raise ValueError('RESOLUTION_REASON_REQUIRED')
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        draft = get_row(conn, 'publisher_drafts', int(draft_id))
        media = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
        if (data.get('media_asset_id'), data.get('account_id')) != (media['id'], draft['account_id']):
            raise ValueError('RESOLUTION_IDENTITY_CHANGED')
        context = reconciliation_context(conn, draft, media)
        if not context:
            raise ValueError('DRAFT_NOT_RESOLUTION_ELIGIBLE')
        checkpoint = conn.execute(
            "SELECT id FROM publisher_execution_logs WHERE draft_id=? AND step IN "
            "('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED') ORDER BY id DESC LIMIT 1",
            (draft['id'],),
        ).fetchone()
        if not checkpoint or data.get('checkpoint_id') != checkpoint['id']:
            raise ValueError('RESOLUTION_CHECKPOINT_CHANGED')
        evidence = get_latest_reconciliation_evidence(conn, draft_id=draft['id'], execution_id=context['execution_id'])
        if decision == 'CONFIRM_EXISTS':
            if not evidence or evidence['id'] != data.get('evidence_id'):
                raise ValueError('CURRENT_STRONG_EVIDENCE_REQUIRED')
            checks = json.loads(draft['checklist_json'] or '{}')
            # Do not fabricate upload/caption/settings checkpoints from a click.
            checks.update(durable=True, tiktok_draft_url='https://www.tiktok.com/tiktokstudio/content')
            reconcile_received(conn, draft['id'], checks, evidence_id=evidence['id'], caller_transaction=True)
        elif decision == 'ABANDON':
            if data.get('acknowledge_remote_may_exist') is not True:
                raise ValueError('REMOTE_DUPLICATE_RISK_ACKNOWLEDGEMENT_REQUIRED')
            if media.get('remote_deleted_at') or media.get('consumed_by_draft_id') is not None:
                raise ValueError('MEDIA_ALREADY_CONSUMED')
            if conn.execute("SELECT 1 FROM publisher_drafts WHERE media_asset_id=? AND id<>? AND status<>'ARCHIVED'",
                            (media['id'], draft['id'])).fetchone():
                raise ValueError('MEDIA_RESERVED_BY_ANOTHER_DRAFT')
            # Archive the old identity while retaining all remote-effect checkpoints.
            # Generic transition/reset remains closed to every ambiguous draft.
            conn.execute("UPDATE publisher_drafts SET status='ARCHIVED',updated_at=? WHERE id=?", (now_iso(), draft['id']))
            conn.execute("UPDATE publisher_media_assets SET status='AVAILABLE',updated_at=? WHERE id=?", (now_iso(), media['id']))
        if decision in {'CONFIRM_EXISTS', 'ABANDON'}:
            item_status = 'SUCCEEDED' if decision == 'CONFIRM_EXISTS' else 'FAILED'
            item_error = None if decision == 'CONFIRM_EXISTS' else 'ADMINISTRATIVELY_CLOSED_REMOTE_UNCONFIRMED'
            conn.execute('UPDATE publisher_execution_items SET status=?,error_code=? WHERE execution_id=? AND draft_id=?',
                         (item_status,item_error,context['execution_id'],draft['id']))
            if not conn.execute("SELECT 1 FROM publisher_execution_items WHERE execution_id=? AND status NOT IN ('SUCCEEDED','FAILED')",
                                (context['execution_id'],)).fetchone():
                conn.execute("UPDATE publisher_batch_executions SET status='COMPLETED',finished_at=? WHERE id=?",
                             (now_iso(),context['execution_id']))
        after = get_row(conn, 'publisher_drafts', draft['id'])
        media_after = get_row(conn, 'publisher_media_assets', media['id'])
        reuse_blocker = media_history_blocker(conn, media_after, account_id=draft['account_id'], purpose='reserve')
        audit = dict(human_confirmed=True, decision=decision, reason=reason,
                     draft_id=draft['id'], media_asset_id=media['id'], account_id=draft['account_id'],
                     execution_id=context['execution_id'], checkpoint_id=checkpoint['id'],
                     evidence_id=evidence['id'] if evidence else None,
                     previous_state=draft['status'], resulting_state=after['status'],
                     previous_media_state=media['status'], resulting_media_state=media_after['status'],
                     remote_may_exist_acknowledged=data.get('acknowledge_remote_may_exist') is True,
                     reuse_authorized=False, reuse_blocker=reuse_blocker,
                     local_task_closed=decision in {'CONFIRM_EXISTS','ABANDON'},
                     persistence_confirmed=decision == 'CONFIRM_EXISTS', drive_deletion_authorized=False,
                     observed_at=now_iso())
        log(conn, draft['id'], 'MANUAL_RESOLUTION', after['status'], json.dumps(audit, ensure_ascii=True))
    return {'draft_id': draft['id'], 'status': after['status'], 'media_status': media_after['status'],
            'decision': decision, 'remote_actions': 0, 'retry_authorized': False,
            'reuse_blocked': reuse_blocker is not None, 'reuse_blocker': reuse_blocker}
