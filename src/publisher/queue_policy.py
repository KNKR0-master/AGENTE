"""Local queue actions; the worker remains the authority for remote mutations."""


REMOTE_STEPS = ('UPLOAD_STARTED', 'SAVE_DRAFT', 'TIKTOK_DRAFT_CONFIRMED')
ACTIVE_ITEM_STATES = ('QUEUED', 'PROCESSING', 'CLEANUP', 'WAITING_MANUAL')


def draft_queue_presentation(draft, media=None):
    if draft.get('upload_status') == 'TRANSFER_UNKNOWN':
        return {'label': 'Envio a verificar', 'kind': 'attention',
                'message': 'O TikTok pode já ter recebido este vídeo. Não envie novamente enquanto a verificação estiver pendente.'}
    if media and media.get('status') == 'NEEDS_RECONCILIATION':
        return {'label': 'Requer verificação', 'kind': 'attention',
                'message': 'Ainda não foi possível confirmar o resultado no TikTok. Verifique o status antes de qualquer nova ação.'}
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
    return {'label': label, 'kind': kind, 'message': ''}


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
    active = bool(execution and execution['item_status'] in ACTIVE_ITEM_STATES)
    pending = draft['status'] in ('DRAFT', 'CAPTION_MISSING') and not execution and not remote_possible
    batch_executed = bool(conn.execute(
        'SELECT 1 FROM publisher_execution_items i '
        'JOIN publisher_drafts d ON d.id=i.draft_id WHERE d.batch_id=? LIMIT 1',
        (draft['batch_id'],),
    ).fetchone()) if draft.get('batch_id') is not None else False
    return {
        'can_edit': pending or (not execution and not remote_possible and draft['status'] == 'CAPTION_MISSING'),
        'can_reorder': pending and not batch_executed,
        'can_remove': pending and (media is None or media['status'] == 'RESERVED'),
        'can_start': (draft['status'] == 'DRAFT' and pending and not account_blocked
                      and media is not None and media['status'] in ('AVAILABLE', 'RESERVED')
                      and bool(str(draft.get('caption') or '').strip())),
        'can_verify': remote_possible and draft.get('persistence_status') != 'PERSISTED',
        'can_reconcile': remote_possible and draft.get('persistence_status') != 'PERSISTED',
        'remote_effect_possible': remote_possible,
        'execution_active': active,
    }
