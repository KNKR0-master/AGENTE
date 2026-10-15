"""Supervised persistence proof; captions and a generic Studio URL grant nothing."""
import hashlib
import json
import hmac
import re
from datetime import datetime, timedelta, timezone

STEP = 'PERSISTENCE_IDENTITY_CONFIRMED'

def persistence_binding(conn, draft_id):
    draft = conn.execute('SELECT * FROM publisher_drafts WHERE id=?',(draft_id,)).fetchone()
    if not draft: raise ValueError('PERSISTENCE_IDENTITY_REQUIRED')
    media = conn.execute('SELECT * FROM publisher_media_assets WHERE id=?',(draft['media_asset_id'],)).fetchone()
    account = conn.execute('SELECT * FROM publisher_accounts WHERE id=?',(draft['account_id'],)).fetchone()
    if not media or not account: raise ValueError('PERSISTENCE_IDENTITY_REQUIRED')
    source = conn.execute('SELECT * FROM publisher_upload_sources WHERE id=? AND draft_id=?',
                          (draft['prepared_upload_source_id'],draft_id)).fetchone()
    checkpoint=conn.execute("SELECT id FROM publisher_execution_logs WHERE draft_id=? AND step IN ('UPLOAD_STARTED','SAVE_DRAFT','TIKTOK_DRAFT_CONFIRMED') ORDER BY id DESC LIMIT 1",(draft_id,)).fetchone()
    execution=conn.execute('SELECT execution_id FROM publisher_execution_items WHERE draft_id=? ORDER BY execution_id DESC LIMIT 1',(draft_id,)).fetchone()
    mutations=[dict(row) for row in conn.execute('SELECT id,account_id,mutation_type,status,started_at,completed_at FROM account_mutation_ledger WHERE draft_id=? ORDER BY id',(draft_id,))]
    mutation_sha256=hashlib.sha256(json.dumps(mutations,sort_keys=True).encode()).hexdigest()
    return dict(mutation_ledger_sha256=mutation_sha256,remote_checkpoint_id=checkpoint['id'] if checkpoint else None,
                execution_id=execution['execution_id'] if execution else None,draft_id=draft_id,account_id=account['id'],account_handle=account['tiktok_username'],
                media_asset_id=media['id'],provider=media['provider'],source_account=media['source_account'],
                remote_file_id=media['remote_file_id'],checksum=media['checksum'],size_bytes=media['size_bytes'],
                caption_sha256=hashlib.sha256(draft['caption'].encode()).hexdigest(),
                snapshot_sha256=hashlib.sha256((draft['headline_snapshot_json'] or '').encode()).hexdigest(),
                upload_source_id=source['id'] if source else None,
                source_sha256=source['source_sha256'] if source else None,
                artifact_sha256=source['artifact_sha256'] if source else None)

def trusted_receipt_keys(conn):
    """No current Studio collector is a trusted issuer.

    A future independently verified adapter needs an explicitly pinned authority.
    HTTP data, Studio mappings and environment flags cannot supply trust anchors.
    Isolated tests replace this function for their own temporary database only.
    """
    return {}


def receipt_bytes(receipt):
    return json.dumps({k: v for k, v in receipt.items() if k != 'signature'},
                      sort_keys=True, separators=(',', ':'), allow_nan=False).encode()


def verify_remote_receipt(conn, binding, receipt, *, fresh=True):
    from src.publisher.reconciliation_evidence import _instant
    error = 'PERSISTENCE_REMOTE_PROOF_INVALID'
    if not isinstance(receipt, dict):
        raise ValueError(error)
    issuer = receipt.get('issuer')
    if not isinstance(issuer, str):
        raise ValueError(error)
    key = trusted_receipt_keys(conn).get(issuer)
    if key is None:
        raise ValueError('PERSISTENCE_REMOTE_MEDIA_IDENTITY_UNAVAILABLE')
    signature = receipt.get('signature')
    if not isinstance(signature, str) or not hmac.compare_digest(
            signature, hmac.new(key, receipt_bytes(receipt), hashlib.sha256).hexdigest()):
        raise ValueError(error)
    remote_id = receipt.get('remote_draft_id')
    media_hash = binding['artifact_sha256'] or binding['source_sha256']
    if (type(receipt.get('version')) is not int or receipt['version'] != 1
            or receipt_bytes({'identity': receipt.get('identity')}) != receipt_bytes({'identity': binding})
            or receipt.get('operation') != 'SAVE_DRAFT' or receipt.get('unique') is not True
            or not isinstance(remote_id, str) or not re.fullmatch(r'[a-f0-9]{32,64}', remote_id)
            or len(set(remote_id)) < 2
            or not re.fullmatch(r'[a-f0-9]{32}', str(receipt.get('nonce', '')))
            or not isinstance(media_hash, str) or not re.fullmatch(r'[a-f0-9]{64}', media_hash)
            or receipt.get('remote_media_sha256') != media_hash
            or binding['remote_checkpoint_id'] is None or binding['execution_id'] is None
            or binding['upload_source_id'] is None):
        raise ValueError(error)
    issued, expires = _instant(receipt.get('issued_at')), _instant(receipt.get('expires_at'))
    if not timedelta(0) < expires - issued <= timedelta(minutes=5):
        raise ValueError(error)
    checkpoint = conn.execute('SELECT created_at FROM publisher_execution_logs WHERE id=?',
                              (binding['remote_checkpoint_id'],)).fetchone()
    if not checkpoint or issued < _instant(checkpoint['created_at']):
        raise ValueError(error)
    if fresh and not issued <= datetime.now(timezone.utc) < expires:
        raise ValueError('PERSISTENCE_REMOTE_PROOF_EXPIRED')
    # A specific remote identity cannot confirm a second local operation.
    for row in conn.execute('SELECT draft_id,message FROM publisher_execution_logs WHERE step=? AND status=?', (STEP, 'OK')):
        try: prior = json.loads(row['message']).get('receipt', {})
        except (ValueError, TypeError, AttributeError): continue
        collision = (prior.get('nonce') == receipt['nonce'] or
                     (prior.get('identity', {}).get('account_id') == binding['account_id']
                      and prior.get('remote_draft_id') == remote_id))
        if (prior.get('issuer') == receipt['issuer'] and collision
                and (row['draft_id'] != binding['draft_id'] or prior.get('identity') != binding)):
            raise ValueError('PERSISTENCE_REMOTE_PROOF_REPLAYED')
    return receipt


def persistence_identity_verified(conn, draft):
    if draft.get('upload_status') != 'TRANSFER_CONFIRMED' or draft.get('processing_status') != 'MEDIA_READY_FOR_EDIT':
        return False
    try:
        binding = persistence_binding(conn, draft['id'])
        for row in conn.execute('SELECT message FROM publisher_execution_logs WHERE draft_id=? AND step=? AND status=? ORDER BY id DESC', (draft['id'], STEP, 'OK')):
            try:
                proof = json.loads(row['message'])
                if proof.get('identity') == binding and proof.get('supervised') is True:
                    receipt = verify_remote_receipt(conn, binding, proof.get('receipt'), fresh=False)
                    if draft.get('tiktok_draft_id') == receipt['remote_draft_id']:
                        return True
            except (TypeError, ValueError, AttributeError):
                continue
    except ValueError:
        pass
    return False


def verify_persistence_evidence(conn, draft, evidence_id):
    from src.publisher.reconciliation_evidence import evaluate_reconciliation_evidence_validity
    evidence = conn.execute('SELECT * FROM publisher_reconciliation_evidence WHERE id=?', (evidence_id,)).fetchone()
    if not evidence: raise ValueError('PERSISTENCE_IDENTITY_EVIDENCE_REQUIRED')
    valid = evaluate_reconciliation_evidence_validity(conn,evidence_id,draft_id=draft['id'],
        media_asset_id=draft['media_asset_id'],account_id=draft['account_id'],execution_id=evidence['execution_id'])
    account = conn.execute('SELECT * FROM publisher_accounts WHERE id=?',(draft['account_id'],)).fetchone()
    if (not valid['valid'] or evidence['outcome'] != 'REMOTE_EXISTS_CONFIRMED'
            or evidence['coverage_status'] != 'COMPLETE' or evidence['candidate_count'] != 1
            or evidence['blocker_code'] is not None):
        raise ValueError('PERSISTENCE_IDENTITY_EVIDENCE_REQUIRED')
    if (not account['session_verified'] or account['account_identity'] != 'ACCOUNT_CONFIRMED'
            or not account['tiktok_username'] or account['tiktok_username'] != evidence['expected_account']
            or evidence['observed_account'] != account['tiktok_username']):
        raise ValueError('PERSISTENCE_ACCOUNT_IDENTITY_REQUIRED')
    binding = persistence_binding(conn, draft['id'])
    if not binding['upload_source_id']: raise ValueError('PERSISTENCE_UPLOAD_SOURCE_REQUIRED')
    source = conn.execute('SELECT execution_id FROM publisher_upload_sources WHERE id=?',(binding['upload_source_id'],)).fetchone()
    if source['execution_id'] != binding['execution_id']:
        raise ValueError('PERSISTENCE_REMOTE_PROOF_INVALID')
    receipt = json.loads(evidence['evidence_payload_json']).get('remote_identity_receipt')
    if receipt is None: raise ValueError('PERSISTENCE_REMOTE_MEDIA_IDENTITY_UNAVAILABLE')
    receipt = verify_remote_receipt(conn, binding, receipt)
    if hashlib.sha256(receipt_bytes(receipt)).hexdigest() != evidence['matched_candidate_fingerprint']:
        raise ValueError('PERSISTENCE_REMOTE_PROOF_INVALID')
    return dict(identity=binding, supervised=True, evidence_id=evidence_id, receipt=receipt)


def can_confirm_persistence(conn, draft, evidence_id):
    try:
        verify_persistence_evidence(conn, draft, evidence_id)
        return True
    except (ValueError, TypeError, KeyError):
        return False


def record_trusted_persistence_evidence(conn, draft_id, receipt):
    """Record an adapter-issued receipt; never mint identity from human input."""
    from src.publisher.reconciliation_evidence import record_reconciliation_evidence
    binding = persistence_binding(conn, draft_id)
    verify_remote_receipt(conn, binding, receipt)
    return record_reconciliation_evidence(conn, draft_id=draft_id,
        media_asset_id=binding['media_asset_id'], account_id=binding['account_id'],
        execution_id=binding['execution_id'], expected_account=binding['account_handle'],
        observed_account=receipt['identity']['account_handle'], outcome='REMOTE_EXISTS_CONFIRMED',
        coverage_status='COMPLETE', candidate_count=1, blocker_code=None,
        source_surface='INDEPENDENT_REMOTE_IDENTITY', matcher_version='remote-receipt-v1',
        matched_candidate_fingerprint=hashlib.sha256(receipt_bytes(receipt)).hexdigest(),
        remote_effect_checkpoint_id=binding['remote_checkpoint_id'],
        verification_started_at=receipt['issued_at'], verification_completed_at=receipt['issued_at'],
        valid_until=receipt['expires_at'], evidence_payload={'remote_identity_receipt': receipt})
