"""Independent signed Fake receipts in isolated SQLite fixtures only.

Positive fixtures use the domain's supervised transactional promotion. They do
not seed PERSISTED and cannot register an authority in a real Studio context.
"""
import hashlib
import json
from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo
from src.store import now_iso
from src.publisher.service import get_row, log
from src.publisher.persistence_identity import persistence_binding
from src.publisher.reconciliation_evidence import record_reconciliation_evidence
from src.publisher.worker import assess_remote_draft_rows

def install_fake_authority(fixture):
    """Trust injected by test code for one temporary SQLite database, never config."""
    if hasattr(fixture, '_fake_receipt_key'):
        return
    import secrets
    from unittest.mock import patch
    from src.publisher import persistence_identity
    fixture._fake_receipt_key = secrets.token_bytes(32)
    database = str(fixture.conn.execute('PRAGMA database_list').fetchone()['file'])
    original = persistence_identity.trusted_receipt_keys
    def keys(conn):
        current = str(conn.execute('PRAGMA database_list').fetchone()['file'])
        return {'isolated-fake-reader': fixture._fake_receipt_key} if current == database else original(conn)
    trusted = patch.object(persistence_identity, 'trusted_receipt_keys', side_effect=keys)
    trusted.start()
    fixture.addCleanup(trusted.stop)


class FakeIndependentMediaReader:
    """Independent synthetic remote store: specific identity plus observed bytes."""
    def __init__(self, fixture, media_bytes=b'synthetic verified media'):
        install_fake_authority(fixture)
        self.fixture, self.media_bytes = fixture, media_bytes

    def attest(self, binding, **changes):
        import hmac, secrets
        from src.publisher.persistence_identity import receipt_bytes
        now = datetime.now(timezone.utc)
        observed_hash = hashlib.sha256(self.media_bytes).hexdigest()
        if observed_hash != (binding['artifact_sha256'] or binding['source_sha256']):
            raise AssertionError('Fake independent media identity mismatch')
        receipt = dict(version=1, issuer='isolated-fake-reader', identity=binding,
            remote_draft_id=hashlib.sha256(('remote:'+json.dumps(binding,sort_keys=True)).encode()).hexdigest(),
            operation='SAVE_DRAFT', unique=True, remote_media_sha256=observed_hash,
            nonce=secrets.token_hex(16), issued_at=now.isoformat(),
            expires_at=(now+timedelta(minutes=5)).isoformat())
        receipt.update(changes)
        receipt['signature'] = hmac.new(self.fixture._fake_receipt_key, receipt_bytes(receipt), hashlib.sha256).hexdigest()
        return receipt


CHECKS = dict(file=True,upload=True,caption=True,settings=True,durable=True,
              tiktok_draft_url='https://www.tiktok.com/tiktokstudio/content')

def uncertain(fixture, draft_id, *, auto_cleanup=0):
    conn=fixture.conn
    draft=get_row(conn,'publisher_drafts',draft_id)
    saved=datetime.now(timezone.utc).replace(second=0,microsecond=0)-timedelta(minutes=1)
    with conn:
        conn.execute("UPDATE publisher_accounts SET tiktok_username='synthetic_account',session_verified=1,account_identity='ACCOUNT_CONFIRMED' WHERE id=?",(draft['account_id'],))
        execution=conn.execute("INSERT INTO publisher_batch_executions(batch_id,total,status,started_at,finished_at,auto_cleanup) VALUES (?,1,'PAUSED',?,?,?)",
                              (draft['batch_id'],(saved-timedelta(seconds=1)).isoformat(),now_iso(),auto_cleanup)).lastrowid
        conn.execute("INSERT INTO publisher_execution_items(execution_id,draft_id,status) VALUES (?,?,'WAITING_MANUAL')",(execution,draft_id))
        conn.execute("UPDATE publisher_drafts SET status='MANUAL_ACTION_REQUIRED',upload_status='TRANSFER_CONFIRMED',processing_status='MEDIA_READY_FOR_EDIT',persistence_status='UNCONFIRMED',checklist_json=? WHERE id=?",(json.dumps(dict(CHECKS,durable=False)),draft_id))
        conn.execute("UPDATE publisher_media_assets SET status='NEEDS_RECONCILIATION' WHERE id=?",(draft['media_asset_id'],))
        log(conn,draft_id,'UPLOAD_STARTED','OK','synthetic transfer')
        log(conn,draft_id,'TRANSFER_CONFIRMED','OK','synthetic transfer')
        checkpoint=conn.execute("INSERT INTO publisher_execution_logs(draft_id,step,status,message,created_at) VALUES (?,'SAVE_DRAFT','OK','synthetic save',?)",(draft_id,saved.isoformat())).lastrowid
        source=conn.execute("INSERT INTO publisher_upload_sources(draft_id,execution_id,kind,source_sha256,upload_filename,probe_json,created_at) VALUES (?,?,'ORIGINAL',?,'synthetic.mp4',?,?)",(draft_id,execution,hashlib.sha256(b'synthetic verified media').hexdigest(),json.dumps(dict(duration=10)),saved.isoformat())).lastrowid
        conn.execute('UPDATE publisher_drafts SET prepared_upload_source_id=? WHERE id=?',(source,draft_id))
    local=(saved+timedelta(minutes=1)).astimezone(ZoneInfo('America/Sao_Paulo'))
    months=('jan','fev','mar','abr','mai','jun','jul','ago','set','out','nov','dez')
    row=dict(caption_text=draft['caption'],duration_text='00:10',updated_text=f'{local.day} de {months[local.month-1]} de {local.year}, {local:%H:%M}')
    return dict(execution=execution,checkpoint=checkpoint,source=source,saved=saved,row=row)

def observation(fixture,draft_id,context,*,rows=None,payload_override=None):
    conn=fixture.conn;draft=get_row(conn,'publisher_drafts',draft_id)
    rows=rows if rows is not None else [context['row']]
    assessment=assess_remote_draft_rows(rows,draft['caption'],context['saved'].isoformat(),10,listing_complete=True)
    source=conn.execute('SELECT * FROM publisher_upload_sources WHERE id=?',(context['source'],)).fetchone()
    payload=dict(candidate_summaries=rows,pagination_observations=dict(complete=True),upload_source_identity=dict(upload_source_id=source['id'],source_sha256=source['source_sha256']))
    if payload_override: payload.update(payload_override)
    now=datetime.now(timezone.utc)
    return record_reconciliation_evidence(conn,draft_id=draft_id,media_asset_id=draft['media_asset_id'],account_id=draft['account_id'],execution_id=context['execution'],expected_account='synthetic_account',observed_account='synthetic_account',source_surface='TIKTOK_STUDIO_DRAFT_LIST',matcher_version='canonical-v1-strong',remote_effect_checkpoint_id=context['checkpoint'],verification_started_at=now.isoformat(),verification_completed_at=now.isoformat(),valid_until=(now+timedelta(minutes=5)).isoformat(),evidence_payload=payload,**assessment)

def trusted_observation(fixture, draft_id, context=None, *, media_bytes=b'synthetic verified media'):
    from src.publisher.persistence_identity import record_trusted_persistence_evidence
    binding = persistence_binding(fixture.conn, draft_id)
    receipt = FakeIndependentMediaReader(fixture, media_bytes).attest(binding)
    return record_trusted_persistence_evidence(fixture.conn, draft_id, receipt)


def confirm_independent_fake_identity(fixture,draft_id):
    context=uncertain(fixture,draft_id)
    evidence=trusted_observation(fixture,draft_id,context)
    from src.publisher.manual_resolution import resolve_draft
    draft=get_row(fixture.conn,'publisher_drafts',draft_id)
    result=resolve_draft(fixture.conn,draft_id,dict(decision='CONFIRM_EXISTS',human_confirmed=True,
        reason='Independent synthetic reader verified specific remote identity and media',
        account_id=draft['account_id'],media_asset_id=draft['media_asset_id'],
        checkpoint_id=context['checkpoint'],evidence_id=evidence))
    assert result['status']=='READY_FOR_REVIEW'
    return context


def cleanup_consent(fixture, draft_id):
    from src.publisher.cleanup_authorization import authorize_cleanup, cleanup_identity_token
    return authorize_cleanup(fixture.conn,draft_id,human_confirmed=True,
        identity_token=cleanup_identity_token(fixture.conn,draft_id))


def promote_prepared_fake(fixture,draft_id,media_bytes):
    """Retain the worker's immutable source, checkpoints and completed ledger."""
    conn=fixture.conn
    draft=get_row(conn,'publisher_drafts',draft_id)
    execution=conn.execute('SELECT execution_id FROM publisher_execution_items WHERE draft_id=? ORDER BY execution_id DESC LIMIT 1',(draft_id,)).fetchone()[0]
    with conn:
        conn.execute("UPDATE publisher_accounts SET tiktok_username='synthetic_account',session_verified=1,account_identity='ACCOUNT_CONFIRMED' WHERE id=?",(draft['account_id'],))
        conn.execute("UPDATE publisher_execution_items SET status='WAITING_MANUAL' WHERE execution_id=? AND draft_id=?",(execution,draft_id))
        conn.execute("UPDATE publisher_batch_executions SET status='PAUSED' WHERE id=?",(execution,))
    evidence=trusted_observation(fixture,draft_id,media_bytes=media_bytes)
    from src.publisher.manual_resolution import resolve_draft
    binding=persistence_binding(conn,draft_id)
    resolve_draft(conn,draft_id,dict(decision='CONFIRM_EXISTS',human_confirmed=True,
        reason='Synthetic independent remote byte comparison after supervised review',
        account_id=draft['account_id'],media_asset_id=draft['media_asset_id'],
        checkpoint_id=binding['remote_checkpoint_id'],evidence_id=evidence))
    return evidence
