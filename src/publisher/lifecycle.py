from pathlib import Path
from contextlib import nullcontext
import json
from src.publisher.service import get_row, log, transition
from src.publisher.diagnostics import safe_error
from src.publisher.media import verify_identity
from src.publisher.media_history import assert_media_history_allowed, MediaHistoryBlocked
from src.publisher.cleanup_authorization import assert_cleanup_authorized, CleanupAuthorizationBlocked
from src.store import now_iso


def confirm_received(conn, draft_id, checks):
    """Save/transfer observations never constitute a unique persisted identity."""
    from src.publisher.persistence_identity import persistence_identity_verified
    draft = get_row(conn, 'publisher_drafts', draft_id)
    if draft.get('persistence_status') == 'PERSISTED':
        return persistence_identity_verified(conn, draft)
    observed = dict(checks)
    observed['durable'] = False
    with conn:
        conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',persistence_status='UNCONFIRMED',"
                     "checklist_json=?,error_code='PERSISTENCE_IDENTITY_REQUIRED',updated_at=? WHERE id=?",
                     (json.dumps(observed,sort_keys=True),now_iso(),draft_id))
        conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION',updated_at=? WHERE id=?",
                     (now_iso(),draft['media_asset_id']))
        log(conn,draft_id,'PERSISTENCE_IDENTITY_PENDING','PENDING',
            'Identidade persistente nao comprovada; origem preservada. Nenhum retry autorizado.')
    return False


def reconcile_received(conn, draft_id, checks, *, evidence_id=None, caller_transaction=False):
    """Validate proof and promote atomically, without another upload."""
    if caller_transaction and not conn.in_transaction:
        raise ValueError('RECONCILIATION_TRANSACTION_REQUIRED')
    owns = not conn.in_transaction
    with conn if owns else nullcontext():
        if owns:
            conn.execute('BEGIN IMMEDIATE')
        return _reconcile_received_locked(conn, draft_id, checks, evidence_id=evidence_id)


def _reconcile_received_locked(conn, draft_id, checks, *, evidence_id):
    draft = get_row(conn, 'publisher_drafts', draft_id)
    if draft.get('persistence_status') == 'PERSISTED':
        from src.publisher.persistence_identity import persistence_identity_verified
        if not persistence_identity_verified(conn,draft):
            raise ValueError('PERSISTENCE_IDENTITY_REQUIRED')
        if evidence_id is not None:
            audit = conn.execute("SELECT message FROM publisher_execution_logs WHERE draft_id=? AND step='PERSISTENCE_IDENTITY_CONFIRMED' AND status='OK' ORDER BY id DESC LIMIT 1",(draft_id,)).fetchone()
            if not audit or json.loads(audit['message']).get('evidence_id') != evidence_id:
                raise ValueError('PERSISTENCE_REMOTE_PROOF_REPLAYED')
        return True
    if draft.get('upload_status') != 'TRANSFER_CONFIRMED' or draft.get('processing_status') != 'MEDIA_READY_FOR_EDIT':
        raise ValueError('TRANSFER_CHECKPOINT_REQUIRED')
    if draft.get('status') not in {'MANUAL_ACTION_REQUIRED', 'STUDIO_CHANGED'}:
        raise ValueError('RECONCILIATION_STATUS_INVALID')
    if not all(checks.get(k) is True for k in ('file', 'upload', 'caption', 'settings', 'durable')):
        raise ValueError('Checkpoint TikTok incompleto; origem preservada')
    if not checks.get('tiktok_draft_url'):
        raise ValueError('Referencia persistente do TikTok ausente')
    from src.publisher.persistence_identity import verify_persistence_evidence
    if evidence_id is None:
        raise ValueError('PERSISTENCE_IDENTITY_EVIDENCE_REQUIRED')
    proof = verify_persistence_evidence(conn, draft, evidence_id)
    timestamp = now_iso()
    remote_id = proof['receipt']['remote_draft_id']
    observed = dict(checks, tiktok_draft_id=remote_id)
    conn.execute("UPDATE publisher_drafts SET status='READY_FOR_REVIEW',persistence_status='PERSISTED',"
                 "confirmed_at=?,tiktok_draft_persisted_at=?,upload_confirmed_at=COALESCE(upload_confirmed_at,?),"
                 "caption_applied_at=COALESCE(caption_applied_at,?),checklist_json=?,tiktok_draft_id=?,"
                 "tiktok_draft_url=?,error_code=NULL,updated_at=? WHERE id=?",
                 (timestamp,timestamp,timestamp,timestamp,json.dumps(observed,sort_keys=True),remote_id,
                  checks['tiktok_draft_url'],timestamp,draft_id))
    conn.execute("UPDATE publisher_media_assets SET status='RESERVED',updated_at=? WHERE id=?",
                 (timestamp,draft['media_asset_id']))
    log(conn,draft_id,'PERSISTENCE_IDENTITY_CONFIRMED','OK',json.dumps(proof,sort_keys=True))
    return True


def _remote_state(provider, asset):
    """Return PRESENT or NOT_FOUND without conflating a trashed file with 404."""
    lookup = getattr(provider, 'get_remote_metadata', None)
    if callable(lookup):
        item = lookup(asset['remote_file_id'])
        if item is None:
            return 'NOT_FOUND'
        verify_identity(asset, item)
        return 'PRESENT'
    return 'PRESENT' if provider.exists(asset['remote_file_id']) else 'NOT_FOUND'


def _has_cleanup_intent(conn, draft_id):
    return bool(conn.execute(
        "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? "
        "AND step IN ('REMOTE_DELETE_PENDING','PERMANENT_DELETE_STARTED') "
        "AND status IN ('PENDING','STARTED','OK') LIMIT 1", (draft_id,)).fetchone())


def _mark_consumed(conn, draft_id, asset_id, timestamp=None):
    timestamp = timestamp or now_iso()
    updated = conn.execute(
        "UPDATE publisher_media_assets SET status='CONSUMED',remote_deleted_at=?,cleanup_error=NULL,updated_at=? "
        "WHERE id=? AND status='CONSUMED_PENDING_DELETE'",
        (timestamp, timestamp, asset_id)).rowcount
    if updated != 1:
        raise ValueError('MEDIA_STATE_CHANGED_DURING_CLEANUP')
    log(conn, draft_id, 'REMOTE_DELETE_CONFIRMED', 'OK',
        'Ausencia remota confirmada; metadata preservada')
    log(conn, draft_id, 'MEDIA_CONSUMED', 'OK',
        'MediaAsset marcado como CONSUMED apos confirmacao remota')


def _remove_temp(conn, draft_id, temp_path, root):
    if root is None:
        raise ValueError('TEMP_ROOT_REQUIRED')
    base = (Path(root).resolve() / 'data/publisher/temp').resolve()
    path = Path(temp_path).resolve()
    if not path.is_relative_to(base) or path == base or path.suffix.lower() != '.mp4':
        raise ValueError('UNSAFE_TEMP_PATH')
    path.unlink(missing_ok=True)
    if path.exists():
        raise ValueError('TEMP_DELETE_UNCONFIRMED')
    with conn:
        log(conn, draft_id, 'TEMP_DELETE_CONFIRMED', 'OK',
            'TEMP removido somente depois de CONSUMED confirmado')
    return True


def assert_cleanup_allowed(conn, draft_id, *, expected_draft=None, expected_asset=None):
    """Shared backend gate; callers recheck under the cleanup write lock.

    Bind later checks to the identity originally approved, rather than silently
    adopting a changed MediaAsset or Publisher account between checkpoints.
    """
    draft = get_row(conn, 'publisher_drafts', draft_id)
    asset = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
    if draft.get('persistence_status') != 'PERSISTED' or not draft.get('tiktok_draft_persisted_at'):
        raise ValueError('PERSISTED_CHECKPOINT_REQUIRED')
    if draft['status'] == 'PREPARING' or conn.execute(
            "SELECT 1 FROM publisher_execution_items WHERE draft_id=? AND status IN ('QUEUED','PROCESSING')",
            (draft_id,)).fetchone():
        raise ValueError('UPLOAD_IN_PROGRESS')
    if asset['status'] not in {'RESERVED', 'CONSUMED_PENDING_DELETE', 'CONSUMED'} or \
            asset.get('consumed_by_draft_id') not in {None, draft_id}:
        raise ValueError('MEDIA_NOT_CLEANUP_ELIGIBLE')
    if not asset.get('remote_file_id') or not asset.get('filename') or asset.get('size_bytes') is None:
        raise ValueError('MANUAL_ACTION_REQUIRED')
    assert_media_history_allowed(
        conn, expected_asset if expected_asset is not None else asset,
        draft_id=draft_id,
        account_id=(expected_draft if expected_draft is not None else draft)['account_id'],
        purpose='consume')
    return draft, asset


def cleanup_remote(conn, draft_id, provider=None, root=None, *, identity_validator=None, provider_factory=None, authorization_id=None):
    """Finalize one persisted media asset with crash-safe, 404-aware idempotency.

    The remote call is made while an IMMEDIATE SQLite transaction is held. A
    crash rolls back only the local confirmation; the committed intent remains,
    so a later call can reconcile a post-delete 404 without issuing delete again.
    """
    if identity_validator:
        identity_validator()
    draft, asset = assert_cleanup_allowed(conn, draft_id)
    assert_cleanup_authorized(conn,draft_id,authorization_id)

    def cleanup_provider():
        nonlocal provider
        if provider is None:
            if provider_factory is None:
                raise ValueError('MEDIA_PROVIDER_REQUIRED')
            # A real adapter constructor performs Drive reads/authentication.
            # Instantiate only after the canonical guard under the write lock.
            provider = provider_factory(get_row(conn, 'publisher_media_assets', draft['media_asset_id']))
        return provider

    # Serialize the first observation with creation of the delete intent. If
    # the remote lookup happened before this lock, a concurrent cleanup could
    # delete the file and make the losing caller misclassify that 404 as an
    # ambiguous pre-intent absence.
    if asset['status'] == 'RESERVED':
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if identity_validator:
                identity_validator()
            assert_cleanup_allowed(conn, draft_id, expected_draft=draft, expected_asset=asset)
            assert_cleanup_authorized(conn,draft_id,authorization_id)
            current = get_row(conn, 'publisher_media_assets', asset['id'])
            if current['status'] == 'RESERVED':
                # A 404 before the intent is committed remains ambiguous and
                # must never be promoted to successful cleanup.
                if _remote_state(cleanup_provider(), current) == 'NOT_FOUND':
                    raise ValueError('MANUAL_ACTION_REQUIRED')
                assert_cleanup_allowed(conn,draft_id,expected_draft=draft,expected_asset=asset)
                assert_cleanup_authorized(conn,draft_id,authorization_id)
                timestamp = now_iso()
                conn.execute(
                    "UPDATE publisher_media_assets SET status='CONSUMED_PENDING_DELETE',consumed_by_draft_id=?,updated_at=? WHERE id=?",
                    (draft_id, timestamp, current['id']))
                log(conn, draft_id, 'REMOTE_DELETE_PENDING', 'PENDING',
                    f"Finalizacao explicita para media_asset_id={current['id']}; nenhum upload sera repetido")
            asset = get_row(conn, 'publisher_media_assets', current['id'])

    if asset['status'] == 'CONSUMED':
        return _finish_local_cleanup(conn, draft, root)
    if not _has_cleanup_intent(conn, draft_id):
        raise ValueError('MANUAL_ACTION_REQUIRED')

    try:
        # Keep the committed intent while serializing the remote side effect.
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            if identity_validator:
                identity_validator()
            assert_cleanup_allowed(conn, draft_id, expected_draft=draft, expected_asset=asset)
            assert_cleanup_authorized(conn,draft_id,authorization_id)
            current = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
            if current['status'] == 'CONSUMED':
                pass
            elif current['status'] != 'CONSUMED_PENDING_DELETE' or not _has_cleanup_intent(conn, draft_id):
                raise ValueError('MANUAL_ACTION_REQUIRED')
            elif _remote_state(cleanup_provider(), current) == 'PRESENT':
                if identity_validator:
                    identity_validator()
                assert_cleanup_allowed(conn,draft_id,expected_draft=draft,expected_asset=asset)
                assert_cleanup_authorized(conn,draft_id,authorization_id)
                provider.delete_remote(current)
                if _remote_state(provider, current) != 'NOT_FOUND':
                    raise ValueError('REMOTE_DELETE_NOT_CONFIRMED')
                assert_cleanup_allowed(conn,draft_id,expected_draft=draft,expected_asset=asset)
                assert_cleanup_authorized(conn,draft_id,authorization_id)
                _mark_consumed(conn, draft_id, current['id'])
            else:
                # This is the crash-recovery branch: intent + persisted draft +
                # identity + 404 means the prior delete already took effect.
                assert_cleanup_allowed(conn,draft_id,expected_draft=draft,expected_asset=asset)
                assert_cleanup_authorized(conn,draft_id,authorization_id)
                _mark_consumed(conn, draft_id, current['id'])
    except (MediaHistoryBlocked, CleanupAuthorizationBlocked):
        # A safety block is not a failed remote delete or a completed cleanup.
        # Propagate its explicit reason without rewriting historical evidence.
        raise
    except Exception as exc:
        message = str(safe_error('cleanup', exc))
        with conn:
            conn.execute('UPDATE publisher_media_assets SET cleanup_error=?,updated_at=? WHERE id=?',
                         (message, now_iso(), draft['media_asset_id']))
            log(conn, draft_id, 'REMOTE_DELETE_PENDING', 'PENDING', message)
        return False

    return _finish_local_cleanup(conn, draft, root)


def _finish_local_cleanup(conn, draft, root):
    from src.publisher.upload_preparation import cleanup_derived_after_consumption
    if draft.get('temp_path'):
        _remove_temp(conn, draft['id'], draft['temp_path'], root)
    result = cleanup_derived_after_consumption(conn, draft['id'], root)
    if result:
        with conn:
            log(conn, draft['id'], 'LOCAL_CLEANUP_CONFIRMED', 'OK',
                'Arquivos locais removidos apos consumo confirmado')
    return result
