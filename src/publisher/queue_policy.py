"""Local queue actions; the worker remains the authority for remote mutations."""

from datetime import datetime

from src.publisher import headlines
from src.publisher.media_history import media_history_blocker


REMOTE_STEPS = ('UPLOAD_STARTED', 'SAVE_DRAFT', 'TIKTOK_DRAFT_CONFIRMED')
ACTIVE_ITEM_STATES = ('QUEUED', 'PROCESSING', 'CLEANUP', 'WAITING_MANUAL')
LOCAL_FAILED_CODES = frozenset({'GOOGLE_USER_CREDENTIAL_REFRESH_FAILED'})


def local_failed_item_proven(conn, draft, item):
    """Allowlisted local failure, audited within this stopped execution only."""
    if (draft['status'] != 'FAILED' or draft.get('error_code') not in LOCAL_FAILED_CODES
            or item['status'] != 'FAILED' or item['execution_status'] != 'PAUSED'
            or item['error_code'] not in ('FAILED', draft['error_code'])
            or item['execution_batch_id'] not in (None, draft['batch_id'])
            or not item['finished_at']):
        return False
    count = conn.execute('SELECT COUNT(*) FROM publisher_execution_items WHERE execution_id=?',
                         (item['execution_id'],)).fetchone()[0]
    if item['total'] <= 0 or item['total'] != count:
        return False
    failure = conn.execute(
        "SELECT message,created_at FROM publisher_execution_logs WHERE draft_id=? AND step='prepare' "
        "AND status='FAILED' ORDER BY id DESC LIMIT 1", (draft['id'],),
    ).fetchone()
    if not failure or failure['message'] != draft['error_code']:
        return False
    try:
        start, end, observed = (datetime.fromisoformat(str(value).replace('Z', '+00:00')) for value in
                                (item['started_at'], item['finished_at'], failure['created_at']))
        return all(value.tzinfo is not None for value in (start, end, observed)) and start <= observed <= end
    except (ValueError, TypeError):
        return False


def reconciliation_context(conn, draft, media):
    """A stopped ambiguous item may be inspected/resolved, never retried here."""
    if not media or draft.get('status') not in ('MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED') or (
        draft.get('persistence_status') != 'UNCONFIRMED' or media['status'] != 'NEEDS_RECONCILIATION'
    ):
        return None
    item = conn.execute(
        'SELECT i.execution_id,i.status,e.status execution_status FROM publisher_execution_items i '
        'JOIN publisher_batch_executions e ON e.id=i.execution_id WHERE i.draft_id=? '
        'ORDER BY i.execution_id DESC LIMIT 1', (draft['id'],),
    ).fetchone()
    if not item or item['status'] not in ('WAITING_MANUAL', 'FAILED') or (
        item['execution_status'] not in ('PAUSED', 'COMPLETED', 'SUCCESS')
    ):
        return None
    busy = conn.execute(
        "SELECT 1 FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id "
        "JOIN publisher_batch_executions e ON e.id=i.execution_id WHERE d.media_asset_id=? "
        "AND (i.status IN ('PROCESSING','CLEANUP','QUEUED') OR e.status IN ('RUNNING','QUEUED')) LIMIT 1",
        (media['id'],),
    ).fetchone()
    return None if busy else dict(item)


def queue_preparation_context(conn, draft_id):
    remote_checkpoint = bool(conn.execute(
        "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step IN "
        "('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED','TEST_FINAL_RESULT') LIMIT 1",
        (draft_id,),
    ).fetchone())
    remote_checkpoint = remote_checkpoint or bool(conn.execute(
        'SELECT 1 FROM account_mutation_ledger WHERE draft_id=? LIMIT 1', (draft_id,),
    ).fetchone())
    latest = conn.execute(
        "SELECT step,message FROM publisher_execution_logs WHERE draft_id=? AND step IN "
        "('HEADLINE_PREPARATION_FAILED','HEADLINE_ARTIFACT_READY','UPLOAD_SOURCE_ORIGINAL') "
        "ORDER BY id DESC LIMIT 1", (draft_id,),
    ).fetchone()
    code = str(latest['message']) if latest and latest['step'] == 'HEADLINE_PREPARATION_FAILED' else ''
    draft_row = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (draft_id,)).fetchone()
    history_blocker = None
    local_failed = False
    if draft_row and draft_row['media_asset_id']:
        draft = dict(draft_row)
        media_row = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?',
                                 (draft['media_asset_id'],)).fetchone()
        media = dict(media_row) if media_row else None
        history_blocker = media_history_blocker(conn, media, draft_id=draft_id,
                                               account_id=draft['account_id'])
        item = conn.execute(
            'SELECT i.*,e.status execution_status,e.batch_id execution_batch_id,e.total,e.started_at,e.finished_at '
            'FROM publisher_execution_items i JOIN publisher_batch_executions e ON e.id=i.execution_id '
            'WHERE i.draft_id=? ORDER BY i.execution_id DESC LIMIT 1', (draft_id,),
        ).fetchone()
        local_failed = bool(not history_blocker and item and local_failed_item_proven(conn, draft, item))
    return {'remote_checkpoint': remote_checkpoint,
            'media_history_blocker': history_blocker,
            'local_failed_preupload': local_failed,
            'local_cancelled': bool(conn.execute("SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
                                                "AND step='BATCH_CANCELLED' LIMIT 1", (draft_id,)).fetchone()),
            'local_preparation_error': code if code.startswith('HEADLINE_') and code.replace('_', '').isalnum() else ''}


def preupload_preparation_resume_allowed(conn, execution, items):
    """Only an audited local headline failure may bypass cleanup-on-resume."""
    failure_found = False
    for item in items:
        row = conn.execute('SELECT * FROM publisher_drafts WHERE id=?', (item['draft_id'],)).fetchone()
        if not row:
            return False
        draft = dict(row)
        media = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (draft['media_asset_id'],)).fetchone()
        if (item['status'] != 'WAITING_MANUAL' or draft['status'] != 'DRAFT'
                or draft['upload_status'] != 'NOT_STARTED' or draft['processing_status'] != 'NOT_STARTED'
                or draft['persistence_status'] == 'PERSISTED' or draft.get('confirmed_at')
                or not media or media['status'] != 'AVAILABLE' or media['consumed_by_draft_id'] is not None
                or media['remote_deleted_at'] or queue_preparation_context(conn, draft['id'])['remote_checkpoint']):
            return False
        if media_history_blocker(conn, dict(media), draft_id=draft['id'], account_id=draft['account_id']):
            return False
        if conn.execute('SELECT 1 FROM account_mutation_ledger WHERE draft_id=? LIMIT 1', (draft['id'],)).fetchone():
            return False
        failure = conn.execute(
            "SELECT message FROM publisher_execution_logs WHERE draft_id=? AND step='HEADLINE_PREPARATION_FAILED' "
            "AND created_at>=? ORDER BY id DESC LIMIT 1", (draft['id'], execution['started_at']),
        ).fetchone()
        failure_found = failure_found or bool(failure and str(failure['message']).startswith('HEADLINE_'))
    return bool(items) and failure_found


def draft_queue_presentation(draft, media=None, *, item_status=None, remote_checkpoint=False,
                             local_preparation_error='', local_cancelled=False,
                             media_history_blocker=None, local_failed_preupload=False):
    remote_possible = bool(remote_checkpoint or draft.get('upload_status') not in (None, 'NOT_STARTED')
                           or draft.get('confirmed_at') or draft.get('persistence_status') == 'PERSISTED'
                           or (media and media.get('status') in ('NEEDS_RECONCILIATION', 'CONSUMED_PENDING_DELETE', 'CONSUMED')))
    persistence = {'PERSISTED': 'Persistência confirmada',
                   'UNCONFIRMED': 'Persistência não confirmada' if remote_possible else '',
                   'DRAFT_ALREADY_PERSISTED': 'Rascunho já salvo'}.get(draft.get('persistence_status'), '')
    summary = {'persistence_label': persistence}
    if local_cancelled and draft.get('status') == 'ARCHIVED':
        return {**summary, 'label': 'Cancelado localmente', 'kind': 'neutral',
                'message': 'Reserva liberada; histórico mantido. Nenhum arquivo remoto foi excluído.'}
    if media_history_blocker and draft.get('persistence_status') != 'PERSISTED' and (
            media_history_blocker.get('draft_id') != draft.get('id') or draft.get('status') == 'ARCHIVED'
            or media_history_blocker['code'] in ('MEDIA_IDENTITY_UNPROVEN', 'MEDIA_HISTORY_IDENTITY_UNPROVEN')):
        blocker = media_history_blocker
        context = (f" Draft #{blocker['draft_id']} · Conta #{blocker['account_id']}."
                   if blocker.get('draft_id') is not None else '')
        return {**summary, 'label': 'Novo uso bloqueado pelo histórico', 'kind': 'attention',
                'reason': blocker['code'], 'message': blocker['message'] + context}
    if local_failed_preupload:
        return {**summary, 'label': 'Falha local antes do envio', 'kind': 'attention',
                'reason': 'LOCAL_PREUPLOAD_FAILURE',
                'message': 'Revise a falha do Drive. Para usar a mídia em outro Draft, cancele o trabalho local e libere a mídia.'}
    if local_preparation_error and not remote_possible:
        return {**summary, 'label': 'Falha na preparação da headline', 'kind': 'attention',
                'reason': 'LOCAL_PREPARATION_FAILURE', 'technical_code': local_preparation_error,
                'message': 'O vídeo ainda não foi enviado ao TikTok. A preparação local precisa ser revisada.'}
    if draft.get('upload_status') == 'TRANSFER_UNKNOWN':
        return {**summary, 'label': 'Envio a verificar', 'kind': 'attention',
                'message': 'O TikTok pode já ter recebido este vídeo. Não envie novamente enquanto a verificação estiver pendente.'}
    if media and media.get('status') == 'NEEDS_RECONCILIATION':
        return {**summary, 'label': 'Requer verificação', 'kind': 'attention',
                'message': 'Ainda não foi possível confirmar o resultado no TikTok. Verifique o status antes de qualquer nova ação.'}
    if draft.get('persistence_status') == 'PERSISTED' and draft.get('status') in ('READY_FOR_REVIEW', 'PUBLISHED', 'SCHEDULED'):
        label = {'READY_FOR_REVIEW': 'Salvo no TikTok', 'PUBLISHED': 'Publicado no Studio', 'SCHEDULED': 'Agendado no Studio'}[draft['status']]
        return {**summary, 'label': label, 'kind': 'ready', 'message': ''}
    if item_status == 'FAILED':
        return {**summary, 'label': 'Falhou nesta execução', 'kind': 'attention',
                'message': 'Confira os detalhes antes de qualquer nova ação.'}
    labels = {
        'DRAFT': ('Pendente', 'neutral'),
        'CAPTION_MISSING': ('Legenda necessária', 'attention'),
        'PREPARING': ('Preparando', 'running'),
        'READY_FOR_REVIEW': ('Pronto para revisão', 'ready'),
        'SCHEDULED': ('Agendado no Studio', 'ready'),
        'PUBLISHED': ('Publicado no Studio', 'ready'),
        'ARCHIVED': ('Removido da fila', 'neutral'),
        'NEEDS_LOGIN': ('Conta precisa de login', 'attention'),
        'UPLOAD_FAILED': ('Preparação interrompida', 'attention'),
        'STUDIO_CHANGED': ('Studio precisa de revisão', 'attention'),
        'MANUAL_ACTION_REQUIRED': ('Ação manual necessária', 'attention'),
        'FAILED': ('Preparação interrompida', 'attention'),
        'PRODUCT_NOT_FOUND': ('Produto não encontrado', 'attention'),
        'PRODUCT_NOT_CONFIRMED': ('Produto a confirmar', 'attention'),
    }
    label, kind = labels.get(draft.get('status'), ('Estado a verificar', 'attention'))
    if draft.get('persistence_status') == 'PERSISTED' and draft.get('status') == 'READY_FOR_REVIEW':
        label = 'Salvo no TikTok'
    if item_status == 'SUCCEEDED' and kind != 'attention' and draft.get('persistence_status') != 'PERSISTED':
        label = 'Concluído nesta execução'
    return {**summary, 'label': label, 'kind': kind, 'message': ''}


def draft_queue_capabilities(conn, draft, media=None, *, account_blocked=False):
    draft_id = draft['id']
    execution = conn.execute(
        'SELECT e.status,i.status item_status FROM publisher_execution_items i '
        'JOIN publisher_batch_executions e ON e.id=i.execution_id '
        'WHERE i.draft_id=? ORDER BY e.id DESC LIMIT 1', (draft_id,),
    ).fetchone()
    remote_log = conn.execute(
        'SELECT 1 FROM publisher_execution_logs WHERE draft_id=? '
        'AND step IN (?,?,?) LIMIT 1', (draft_id, *REMOTE_STEPS),
    ).fetchone()
    remote_possible = bool(
        remote_log or draft.get('upload_status') not in (None, 'NOT_STARTED')
        or draft.get('confirmed_at') or draft.get('persistence_status') == 'PERSISTED'
        or (media and media['status'] in ('PROCESSING', 'NEEDS_RECONCILIATION',
                                         'CONSUMED_PENDING_DELETE', 'CONSUMED'))
    )
    history_blocker = media_history_blocker(conn, media, draft_id=draft_id, account_id=draft['account_id']) if draft.get('media_asset_id') else None
    remote_possible = remote_possible or bool(history_blocker)
    active = bool(execution and execution['item_status'] in ACTIVE_ITEM_STATES)
    pending = draft['status'] in ('DRAFT', 'CAPTION_MISSING') and not execution and not remote_possible
    verification_ready = reconciliation_context(conn, draft, media) is not None
    batch_executed = bool(conn.execute(
        'SELECT 1 FROM publisher_execution_items i '
        'JOIN publisher_drafts d ON d.id=i.draft_id WHERE d.batch_id=? LIMIT 1',
        (draft['batch_id'],),
    ).fetchone()) if draft.get('batch_id') is not None else False
    return {
        'can_edit': pending or (not execution and not remote_possible and draft['status'] == 'CAPTION_MISSING'),
        'can_reorder': pending and not batch_executed,
        'can_remove': pending and (media is None or media['status'] in ('RESERVED', 'FAILED'))
                      and (not draft.get('media_asset_id') or local_cancel_blocker(conn, draft) is None),
        'can_start': (draft['status'] == 'DRAFT' and pending and not account_blocked
                      and media is not None and media['status'] in ('AVAILABLE', 'RESERVED')
                      and bool(str(draft.get('caption') or '').strip())
                      and not headlines.readiness(headlines.read_snapshot(draft))),
        'can_verify': verification_ready,
        'can_resolve': verification_ready,
        'can_reconcile': remote_possible and draft.get('persistence_status') != 'PERSISTED',
        'remote_effect_possible': remote_possible,
        'execution_active': active,
        'media_history_blocker': history_blocker,
    }


def batch_queue_capabilities(conn, batch_id):
    """Authoritative local-only capabilities for a batch before execution."""
    batch_id = int(batch_id)
    drafts = [dict(row) for row in conn.execute(
        "SELECT * FROM publisher_drafts WHERE batch_id=? AND status<>'ARCHIVED' ORDER BY queue_position,id",
        (batch_id,),
    )]
    has_execution = bool(conn.execute(
        "SELECT 1 FROM publisher_execution_items i JOIN publisher_drafts d ON d.id=i.draft_id "
        "WHERE d.batch_id=? LIMIT 1", (batch_id,),
    ).fetchone())
    draft_caps = []
    for draft in drafts:
        media = None
        if draft.get('media_asset_id'):
            row = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (draft['media_asset_id'],)).fetchone()
            media = dict(row) if row else None
        draft_caps.append(draft_queue_capabilities(conn, draft, media))

    has_items = bool(drafts)
    pending_only = has_items and not has_execution and all(cap['can_edit'] for cap in draft_caps)
    can_reorder = pending_only and all(cap['can_reorder'] for cap in draft_caps)
    all_drafts_removable = has_items and all(cap['can_remove'] for cap in draft_caps)
    locally_cancelable = [local_cancel_blocker(conn, draft) is None for draft in drafts]
    can_cancel = pending_only and all_drafts_removable and all(locally_cancelable)
    return {
        'can_rename': pending_only,
        'can_edit': pending_only,
        'can_edit_configuration': pending_only,
        'editable_fields': ['account_id', 'preset_id'] if pending_only else [],
        'can_reorder': can_reorder,
        'can_cancel': can_cancel,
        'can_archive': False,
        'has_execution': has_execution,
        'active_draft_count': len(drafts),
        'can_cancel_eligible': any(locally_cancelable),
    }


def local_cancel_blocker(conn, draft):
    """Proof of local-only work; never infer safety from an empty remote list."""
    if draft['status'] not in ('DRAFT', 'CAPTION_MISSING', 'FAILED'):
        return 'DRAFT_STATE_BLOCKED'
    context = queue_preparation_context(conn, draft['id'])
    if (context['remote_checkpoint'] or draft.get('upload_status') not in (None, 'NOT_STARTED')
            or draft.get('processing_status') not in (None, 'NOT_STARTED')
            or draft.get('persistence_status') != 'UNCONFIRMED' or draft.get('confirmed_at')):
        return 'REMOTE_EFFECT_POSSIBLE'
    if conn.execute("SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND UPPER(step) IN "
                    "('UPLOAD','UPLOADED','UPLOAD_CONFIRMED','CAPTION','REMOTE_DELETED') LIMIT 1",
                    (draft['id'],)).fetchone():
        return 'REMOTE_EFFECT_POSSIBLE'
    if conn.execute("SELECT 1 FROM publisher_batch_executions WHERE batch_id=? "
                    "AND status IN ('QUEUED','RUNNING','PROCESSING') LIMIT 1", (draft['batch_id'],)).fetchone():
        return 'EXECUTION_ACTIVE'
    media = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?', (draft.get('media_asset_id'),)).fetchone()
    if (not media or not media['remote_file_id'] or media['status'] not in ('AVAILABLE', 'RESERVED', 'FAILED')
            or media['consumed_by_draft_id'] is not None or media['remote_deleted_at']):
        return 'RESERVATION_IDENTITY_OR_STATE_BLOCKED'
    history_blocker = media_history_blocker(conn, dict(media), draft_id=draft['id'],
                                           account_id=draft['account_id'], purpose='release')
    if history_blocker:
        return history_blocker['code']
    other = conn.execute("SELECT 1 FROM publisher_drafts d JOIN publisher_media_assets m ON m.id=d.media_asset_id "
                         "WHERE d.id<>? AND d.status<>'ARCHIVED' AND (m.id=? OR "
                         "(m.provider=? AND m.source_account=? AND m.remote_file_id=?)) LIMIT 1",
                         (draft['id'], media['id'], media['provider'], media['source_account'], media['remote_file_id'])).fetchone()
    if other:
        return 'OTHER_ACTIVE_RESERVATION'
    if conn.execute("SELECT 1 FROM publisher_execution_items i JOIN publisher_batch_executions e ON e.id=i.execution_id "
                    "JOIN publisher_drafts d ON d.id=i.draft_id JOIN publisher_media_assets m ON m.id=d.media_asset_id "
                    "WHERE (m.id=? OR (m.provider=? AND m.source_account=? AND m.remote_file_id=?)) "
                    "AND (i.status IN ('QUEUED','PROCESSING','CLEANUP') OR e.status IN ('QUEUED','RUNNING','PROCESSING')) LIMIT 1",
                    (media['id'], media['provider'], media['source_account'], media['remote_file_id'])).fetchone():
        return 'EXECUTION_ACTIVE'
    items = conn.execute('SELECT i.*,e.status execution_status,e.started_at,e.finished_at,e.total,e.batch_id execution_batch_id FROM publisher_execution_items i '
                         'JOIN publisher_batch_executions e ON e.id=i.execution_id WHERE i.draft_id=?',
                         (draft['id'],)).fetchall()
    if draft['status'] == 'FAILED' and not items:
        return 'EXECUTION_NOT_PROVEN_LOCAL_ONLY'
    for item in items:
        execution = dict(status=item['execution_status'], started_at=item['started_at'])
        proven = (local_failed_item_proven(conn, draft, item) if draft['status'] == 'FAILED'
                  else preupload_preparation_resume_allowed(conn, execution, [dict(item)]))
        if execution['status'] != 'PAUSED' or not proven:
            return 'EXECUTION_NOT_PROVEN_LOCAL_ONLY'
    return None
