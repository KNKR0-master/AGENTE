"""Local human posting declarations and batch summaries; never publish remotely."""

import json

from src.publisher.service import get_row, log, transition


def posting_eligibility(conn, draft, media):
    if draft['status'] == 'PUBLISHED':
        return False, 'ALREADY_PUBLISHED'
    if draft['status'] == 'ARCHIVED':
        return False, 'ARCHIVED'
    if draft['persistence_status'] != 'PERSISTED' or not draft.get('confirmed_at'):
        return False, 'PERSISTENCE_UNCONFIRMED'
    if draft['status'] not in ('READY_FOR_REVIEW', 'SCHEDULED'):
        return False, 'DRAFT_REQUIRES_REVIEW'
    if draft.get('upload_status') == 'TRANSFER_UNKNOWN' or (media and media['status'] not in (
        'RESERVED', 'CONSUMED_PENDING_DELETE', 'CONSUMED'
    )):
        return False, 'MEDIA_REQUIRES_RECONCILIATION'
    if conn.execute(
        "SELECT 1 FROM publisher_execution_items i JOIN publisher_batch_executions e ON e.id=i.execution_id "
        "WHERE i.draft_id=? AND (i.status IN ('PROCESSING','CLEANUP','QUEUED') "
        "OR e.status IN ('RUNNING','QUEUED')) LIMIT 1", (draft['id'],),
    ).fetchone():
        return False, 'EXECUTION_IN_PROGRESS'
    return True, None


def batch_review(conn, batch_id):
    batch = get_row(conn, 'publisher_batches', int(batch_id))
    items = []
    counts = dict(total=0, prepared=0, attention=0, processing=0, queued=0, published=0, scheduled=0, abandoned=0)
    for row in conn.execute('SELECT * FROM publisher_drafts WHERE batch_id=? ORDER BY queue_position,id', (batch['id'],)):
        draft = dict(row)
        media = get_row(conn, 'publisher_media_assets', draft['media_asset_id']) if draft['media_asset_id'] else None
        eligible, reason = posting_eligibility(conn, draft, media)
        status = draft['status']
        if status == 'ARCHIVED':
            category = 'abandoned'
        elif draft['upload_status'] == 'TRANSFER_UNKNOWN' or (media and media['status'] == 'NEEDS_RECONCILIATION') or (
            status in ('PUBLISHED', 'SCHEDULED') and draft['persistence_status'] != 'PERSISTED'
        ):
            category = 'attention'
        elif status == 'PUBLISHED':
            category = 'published'
        elif status == 'SCHEDULED':
            category = 'scheduled'
        elif status == 'PREPARING' or reason == 'EXECUTION_IN_PROGRESS':
            category = 'processing'
        elif eligible:
            category = 'prepared'
        elif status == 'DRAFT' and draft['upload_status'] == 'NOT_STARTED':
            category = 'queued'
        else:
            category = 'attention'
        counts['total'] += 1
        counts[category] += 1
        items.append(dict(draft_id=draft['id'], filename=draft['filename'], status=status,
                          category=category, eligible=eligible, blocker=reason,
                          persistence_status=draft['persistence_status']))
    resolved = counts['published'] + counts['scheduled'] + counts['abandoned']
    prepared = resolved + counts['prepared']
    return {'batch_id': batch['id'], 'name': batch['name'], **counts, 'items': items,
            'prepared_percent': round(prepared * 100 / counts['total']) if counts['total'] else 0,
            'review_complete': bool(counts['total']) and resolved == counts['total'],
            'eligible_ids': [item['draft_id'] for item in items if item['eligible']]}


def register_batch_published(conn, batch_id, data):
    if data.get('human_confirmed') is not True:
        raise ValueError('EXPLICIT_POSTING_CONFIRMATION_REQUIRED')
    ids = data.get('draft_ids')
    if not isinstance(ids, list) or not ids or len(ids) > 100 or any(type(i) is not int for i in ids):
        raise ValueError('EXPLICIT_DRAFT_SELECTION_REQUIRED')
    results = []
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        get_row(conn, 'publisher_batches', int(batch_id))
        for draft_id in dict.fromkeys(ids):
            row = conn.execute('SELECT * FROM publisher_drafts WHERE id=? AND batch_id=?', (draft_id, int(batch_id))).fetchone()
            if not row:
                results.append({'draft_id': draft_id, 'result': 'SKIPPED', 'reason': 'NOT_IN_BATCH'})
                continue
            draft = dict(row)
            media = get_row(conn, 'publisher_media_assets', draft['media_asset_id']) if draft['media_asset_id'] else None
            eligible, reason = posting_eligibility(conn, draft, media)
            if not eligible:
                results.append({'draft_id': draft_id, 'result': 'SKIPPED', 'reason': reason})
                continue
            transition(conn, draft_id, 'PUBLISHED', checklist=json.loads(draft['checklist_json'] or '{}'))
            log(conn, draft_id, 'MANUAL_POSTING_REVIEW', 'PUBLISHED', json.dumps({
                'human_confirmed': True, 'batch_id': int(batch_id), 'draft_id': draft_id,
                'media_asset_id': draft['media_asset_id'], 'previous_state': draft['status'],
                'resulting_state': 'PUBLISHED', 'remote_actions': 0,
            }))
            results.append({'draft_id': draft_id, 'result': 'REGISTERED', 'reason': None})
    review = batch_review(conn, batch_id)
    return {'results': results, 'registered': sum(r['result'] == 'REGISTERED' for r in results),
            'skipped': sum(r['result'] == 'SKIPPED' for r in results),
            'requires_review': review['attention'], 'review': review, 'remote_actions': 0}
