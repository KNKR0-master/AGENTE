"""Explicit batch consumption through the existing MediaAsset lifecycle.

Requests and item outcomes are append-only in the existing audit log. Publication
declarations, queue visibility and cancellation never authorize this operation.
An interrupted RUNNING item requires reconciliation; it is never retried blindly.
"""

import hashlib
import json
import re
import threading

from src.publisher.lifecycle import cleanup_remote
from src.publisher.cleanup_authorization import authorize_cleanup, cleanup_identity_token
from src.publisher.media import verify_identity
from src.publisher.media_history import media_history_blocker, assert_media_history_allowed
from src.publisher.service import get_row, log


_LOCK = threading.RLock()
REQUEST = 'BATCH_CONSUMPTION_REQUEST'
ITEM = 'BATCH_CONSUMPTION_ITEM'
RECEIPT = 'BATCH_REMOTE_DELETE_ACK'
TERMINAL = {'CONSUMED', 'ALREADY_CONSUMED', 'BLOCKED', 'FAILED'}


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(',', ':'))


def _events(conn, batch_id):
    return [dict(row, payload=json.loads(row['message'])) for row in conn.execute(
        'SELECT * FROM publisher_execution_logs WHERE step IN (?,?) '
        "AND json_extract(message,'$.batch_id')=? ORDER BY id", (REQUEST, ITEM, batch_id))]


def _identity(conn, draft, media):
    account = get_row(conn, 'publisher_accounts', draft['account_id'])
    return {
        'draft': {key: draft.get(key) for key in (
            'id', 'batch_id', 'account_id', 'media_asset_id', 'filename', 'confirmed_at',
            'tiktok_draft_id', 'tiktok_draft_url', 'tiktok_draft_persisted_at')},
        'account': {key: account.get(key) for key in ('id', 'name', 'browser_profile', 'tiktok_username')},
        'media': {key: media.get(key) for key in (
            'id', 'provider', 'source_account', 'remote_file_id', 'remote_folder_id',
            'filename', 'mime_type', 'size_bytes', 'checksum', 'remote_modified_at')},
    }


def _token(identity):
    return hashlib.sha256(_json(identity).encode()).hexdigest()


def consumption_eligibility(conn, draft, media):
    if draft['status'] in {'ARCHIVED', 'ABANDONED'}:
        return 'abandoned', 'ABANDONED_MEDIA_PROTECTED'
    if draft['status'] != 'PUBLISHED':
        return 'blocked', 'PUBLICATION_NOT_REGISTERED'
    if not media:
        return 'blocked', 'MEDIA_ASSET_REQUIRED'
    history_blocker = media_history_blocker(
        conn, media, draft_id=draft['id'], account_id=draft['account_id'], purpose='consume')
    if history_blocker:
        return 'blocked', history_blocker['code']
    if draft['filename'] != media['filename']:
        return 'blocked', 'MEDIA_IDENTITY_CONFLICT'
    if draft.get('upload_status') == 'TRANSFER_UNKNOWN' or media['status'] in {
        'NEEDS_RECONCILIATION', 'RECEIVED_NEEDS_REVIEW', 'PROCESSING'
    }:
        return 'blocked', 'TRANSFER_RECONCILIATION_REQUIRED'
    if draft.get('persistence_status') != 'PERSISTED' or not all(draft.get(key) for key in (
        'confirmed_at', 'tiktok_draft_persisted_at', 'tiktok_draft_url'
    )):
        return 'blocked', 'PERSISTED_CHECKPOINT_REQUIRED'
    if media.get('consumed_by_draft_id') not in {None, draft['id']} or conn.execute(
        "SELECT 1 FROM publisher_drafts WHERE media_asset_id=? AND id<>? AND status<>'ARCHIVED'",
        (media['id'], draft['id']),
    ).fetchone():
        return 'blocked', 'MEDIA_IDENTITY_CONFLICT'
    if media.get('provider') != 'google_drive' or media.get('source_account') != 'cecilia':
        return 'blocked', 'SOURCE_IDENTITY_UNSUPPORTED'
    if not media.get('remote_file_id') or not media.get('remote_folder_id') or not media.get('filename') or media.get('size_bytes') is None:
        return 'blocked', 'SOURCE_IDENTITY_INCOMPLETE'
    if conn.execute(
        "SELECT 1 FROM publisher_execution_items i JOIN publisher_batch_executions e ON e.id=i.execution_id "
        "WHERE i.draft_id=? AND (i.status IN ('QUEUED','PROCESSING','CLEANUP') "
        "OR e.status IN ('QUEUED','RUNNING')) LIMIT 1", (draft['id'],),
    ).fetchone():
        return 'blocked', 'EXECUTION_IN_PROGRESS'
    if media['status'] == 'CONSUMED':
        if not media.get('remote_deleted_at') or media.get('consumed_by_draft_id') != draft['id']:
            return 'blocked', 'CONSUMPTION_CHECKPOINT_INCOMPLETE'
        return 'consumed', None
    if media['status'] not in {'RESERVED', 'CONSUMED_PENDING_DELETE'}:
        return 'blocked', 'MEDIA_NOT_CLEANUP_ELIGIBLE'
    return 'eligible', None


def batch_consumption_review(conn, batch_id):
    batch_id = int(batch_id)
    get_row(conn, 'publisher_batches', batch_id)
    events = _events(conn, batch_id)
    latest, requests = {}, {}
    for event in events:
        payload = event['payload']
        if event['step'] == REQUEST:
            requests[payload['request_id']] = payload
        else:
            latest[(payload['request_id'], event['draft_id'])] = event
    pending = {draft_id: event for (_, draft_id), event in latest.items()
               if event['status'] not in TERMINAL}
    items = []
    counts = dict(eligible=0, consumed=0, blocked=0, abandoned=0, pending=0, local_cleanup_pending=0)
    published_count = 0
    for row in conn.execute('SELECT * FROM publisher_drafts WHERE batch_id=? ORDER BY queue_position,id', (batch_id,)):
        draft = dict(row)
        media = get_row(conn, 'publisher_media_assets', draft['media_asset_id']) if draft['media_asset_id'] else None
        category, blocker = consumption_eligibility(conn, draft, media)
        can_evaluate = category == 'eligible'
        published_count += draft['status'] == 'PUBLISHED'
        identity = _identity(conn, draft, media) if media else None
        if draft['id'] in pending and category not in {'abandoned', 'blocked'}:
            category, blocker = 'pending', 'CONSUMPTION_REQUEST_PENDING'
            can_evaluate = False
        history = [event for (_, draft_id), event in latest.items() if draft_id == draft['id']]
        last = max(history, key=lambda event: event['id']) if history else None
        if last and last['payload'].get('local_cleanup_pending') and conn.execute(
            "SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step='LOCAL_CLEANUP_CONFIRMED' "
            "AND status='OK' AND id>? LIMIT 1", (draft['id'], last['id']),
        ).fetchone():
            last = {**last, 'payload': {**last['payload'], 'local_cleanup_pending': False, 'reason': None}}
        if category == 'eligible' and last and last['status'] in {'BLOCKED', 'FAILED'} and last['payload'].get('identity_token') == _token(identity):
            category, blocker = 'blocked', last['payload'].get('reason') or 'CLEANUP_FAILED'
        if last and last['payload'].get('local_cleanup_pending') and category == 'consumed':
            counts['local_cleanup_pending'] += 1
        counts[category] += 1
        items.append(dict(draft_id=draft['id'], filename=draft['filename'], category=category,
                          blocker=blocker, identity=identity, identity_token=_token(identity) if identity else None,
                          can_evaluate=can_evaluate,
                          cleanup_error=media.get('cleanup_error') if media else None,
                          last_result=last['payload'] if last else None))
    pending_ids = {event['payload']['request_id'] for event in pending.values()}
    return dict(batch_id=batch_id, items=items, counts=counts, published_count=published_count,
                pending_requests=[requests[key] for key in requests if key in pending_ids])


def _record_item(conn, request_id, batch_id, draft_id, status, reason=None, **extra):
    payload = dict(request_id=request_id, batch_id=batch_id, draft_id=draft_id,
                   result=status, reason=reason, **extra)
    log(conn, draft_id, ITEM, status, _json(payload))
    return payload


def _assert_identity(conn, batch_id, draft_id, identity_token):
    draft = get_row(conn, 'publisher_drafts', draft_id)
    media = get_row(conn, 'publisher_media_assets', draft['media_asset_id'])
    if draft['batch_id'] != batch_id or _token(_identity(conn, draft, media)) != identity_token:
        raise ValueError('CONSUMPTION_IDENTITY_CHANGED')
    assert_media_history_allowed(
        conn, media, draft_id=draft_id, account_id=draft['account_id'], purpose='consume')
    category, reason = consumption_eligibility(conn, draft, media)
    if category not in {'eligible', 'consumed'}:
        raise ValueError(reason)
    return draft, media


class _IdentityCheckedProvider:
    """Require metadata and delete acknowledgement before accepting absence.

    A 404 following an intent alone is ambiguous. A successful delete response
    plus the same persisted identity allows reconciliation after a local error.
    Crashes before that receipt is persisted deliberately remain blocked.
    """

    def __init__(self, provider, conn, draft_id, token, asset):
        self.provider, self.conn, self.draft_id, self.token, self.asset = provider, conn, draft_id, token, asset
        self.acknowledged = False
        self.blocker = None

    def get_remote_metadata(self, file_id):
        lookup = getattr(self.provider, 'get_remote_metadata', None)
        if not callable(lookup):
            self.blocker = 'REMOTE_IDENTITY_METADATA_REQUIRED'
            raise ValueError('REMOTE_IDENTITY_METADATA_REQUIRED')
        item = lookup(file_id)
        if item is None:
            receipt = self.conn.execute(
                'SELECT 1 FROM publisher_execution_logs WHERE draft_id=? AND step=? AND status=? '
                "AND json_extract(message,'$.identity_token')=? LIMIT 1",
                (self.draft_id, RECEIPT, 'OK', self.token),
            ).fetchone()
            if not self.acknowledged and not receipt:
                self.blocker = 'REMOTE_ABSENCE_REQUIRES_RECONCILIATION'
                raise ValueError('REMOTE_ABSENCE_REQUIRES_RECONCILIATION')
            return None
        if item.get('trashed'):
            self.blocker = 'REMOTE_TRASH_REQUIRES_RECONCILIATION'
            raise ValueError('REMOTE_TRASH_REQUIRES_RECONCILIATION')
        try:
            verify_identity(self.asset, item)
        except (ValueError, KeyError, TypeError):
            self.blocker = 'REMOTE_SOURCE_IDENTITY_CHANGED'
            raise ValueError(self.blocker) from None
        if item.get('mimeType') != self.asset['mime_type'] or self.asset['remote_folder_id'] not in item.get('parents', []):
            self.blocker = 'REMOTE_SOURCE_IDENTITY_CHANGED'
            raise ValueError('REMOTE_SOURCE_IDENTITY_CHANGED')
        return item

    def delete_remote(self, asset):
        if self.provider.delete_remote(asset) is not True:
            self.blocker = 'REMOTE_DELETE_ACKNOWLEDGEMENT_REQUIRED'
            raise ValueError(self.blocker)
        self.acknowledged = True


def evaluate_batch_consumption(conn, batch_id, provider_factory):
    """Read remote identity for published candidates; never create delete intent."""
    review = batch_consumption_review(conn, batch_id)
    for item in review['items']:
        if not item['can_evaluate']:
            continue
        try:
            _, asset = _assert_identity(conn, review['batch_id'], item['draft_id'], item['identity_token'])
            provider = _IdentityCheckedProvider(provider_factory(asset), conn, item['draft_id'], item['identity_token'], asset)
            provider.get_remote_metadata(asset['remote_file_id'])
            item['category'], item['blocker'] = 'eligible', None
        except ValueError as exc:
            item['category'], item['blocker'] = 'blocked', str(exc)
        except Exception as exc:
            item['category'], item['blocker'] = 'blocked', 'REMOTE_EVALUATION_FAILED'
            item['error_type'] = type(exc).__name__
    for category in ('eligible', 'consumed', 'blocked', 'abandoned', 'pending'):
        review['counts'][category] = sum(item['category'] == category for item in review['items'])
    review['remote_read_only'] = True
    return review


def finalize_batch_consumption(conn, batch_id, data, provider_factory, root):
    """Serialize selections, then invoke cleanup_remote separately for each item."""
    if data.get('drive_delete_confirmed') is not True:
        raise ValueError('SEPARATE_DRIVE_DELETION_AUTHORIZATION_REQUIRED')
    batch_id = int(batch_id)
    request_id = data.get('request_id')
    selections = data.get('items')
    if not isinstance(request_id, str) or not re.fullmatch(r'[a-zA-Z0-9-]{16,80}', request_id):
        raise ValueError('CONSUMPTION_REQUEST_ID_REQUIRED')
    if not isinstance(selections, list) or not 1 <= len(selections) <= 100 or any(
        not isinstance(item, dict) or type(item.get('draft_id')) is not int or
        not isinstance(item.get('identity_token'), str) for item in selections
    ) or len({item['draft_id'] for item in selections}) != len(selections):
        raise ValueError('EXPLICIT_MEDIA_SELECTION_REQUIRED')
    if type(data.get('confirmed_count')) is not int or data['confirmed_count'] != len(selections):
        raise ValueError('CONFIRMED_MEDIA_COUNT_MISMATCH')
    selections = sorted([dict(draft_id=item['draft_id'], identity_token=item['identity_token'])
                         for item in selections], key=lambda item: item['draft_id'])
    with _LOCK:
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            review = batch_consumption_review(conn, batch_id)
            by_id = {item['draft_id']: item for item in review['items']}
            if any(item['draft_id'] not in by_id for item in selections):
                raise ValueError('MEDIA_NOT_IN_BATCH')
            existing = conn.execute(
                'SELECT message FROM publisher_execution_logs WHERE step=? '
                "AND json_extract(message,'$.request_id')=? LIMIT 1", (REQUEST, request_id),
            ).fetchone()
            header = dict(request_id=request_id, batch_id=batch_id, items=selections,
                          drive_delete_confirmed=True, confirmed_count=len(selections))
            if existing:
                if json.loads(existing['message']) != header:
                    raise ValueError('CONSUMPTION_REQUEST_ID_REUSED')
            else:
                log(conn, selections[0]['draft_id'], REQUEST, 'AUTHORIZED', _json(header))
                for selection in selections:
                    item = by_id[selection['draft_id']]
                    reason = item['blocker']
                    if item['identity_token'] != selection['identity_token']:
                        status, reason = 'BLOCKED', 'CONSUMPTION_IDENTITY_CHANGED'
                    else:
                        status = {'eligible': 'QUEUED', 'consumed': 'ALREADY_CONSUMED'}.get(item['category'], 'BLOCKED')
                        if item['can_evaluate']:
                            status, reason = 'QUEUED', None
                        if status == 'ALREADY_CONSUMED' and (item.get('last_result') or {}).get('local_cleanup_pending'):
                            status, reason = 'BLOCKED', 'LOCAL_CLEANUP_PENDING'
                    _record_item(conn, request_id, batch_id, item['draft_id'], status, reason,
                                 identity_token=selection['identity_token'], identity=item['identity'])
        for selection in selections:
            draft_id, token = selection['draft_id'], selection['identity_token']
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                last = conn.execute(
                    'SELECT status FROM publisher_execution_logs WHERE draft_id=? AND step=? '
                    "AND json_extract(message,'$.request_id')=? ORDER BY id DESC LIMIT 1",
                    (draft_id, ITEM, request_id),
                ).fetchone()
                if last and last['status'] == 'RUNNING':
                    # A persisted CONSUMED checkpoint permits local-only
                    # recovery. Every other interrupted state needs review.
                    try:
                        _, recovered = _assert_identity(conn, batch_id, draft_id, token)
                    except ValueError as exc:
                        if getattr(exc, 'code', None):
                            # Keep the interrupted claim pending, but expose the
                            # historical safety reason without retrying it.
                            _record_item(conn, request_id, batch_id, draft_id, 'RUNNING',
                                         exc.code, identity_token=token)
                        continue
                    if recovered['status'] != 'CONSUMED':
                        continue
                elif not last or last['status'] != 'QUEUED':
                    continue
                _record_item(conn, request_id, batch_id, draft_id, 'RUNNING', identity_token=token)
            provider, asset = None, None
            status, reason = 'FAILED', None
            try:
                _, asset = _assert_identity(conn, batch_id, draft_id, token)
                def checked_provider(current_asset):
                    nonlocal provider
                    provider = _IdentityCheckedProvider(
                        provider_factory(current_asset), conn, draft_id, token, current_asset)
                    return provider
                authorization_id = authorize_cleanup(conn,draft_id,
                    human_confirmed=data['drive_delete_confirmed'],
                    identity_token=cleanup_identity_token(conn,draft_id),request_id=request_id,
                    identity_validator=lambda: _assert_identity(conn,batch_id,draft_id,token))
                ok = cleanup_remote(conn, draft_id, root=root, provider_factory=checked_provider,
                                    authorization_id=authorization_id,
                                    identity_validator=lambda: _assert_identity(conn, batch_id, draft_id, token))
                current = get_row(conn, 'publisher_media_assets', asset['id'])
                status = 'CONSUMED' if ok and current['status'] == 'CONSUMED' else 'FAILED'
                reason = None if status == 'CONSUMED' else (provider.blocker if provider else None) or 'CLEANUP_FAILED'
            except ValueError as exc:
                status, reason = 'BLOCKED', getattr(exc, 'code', str(exc))
            except Exception as exc:
                # Never put credentials/provider exception text in the batch UI.
                reason = type(exc).__name__
            with conn:
                if provider and provider.acknowledged:
                    log(conn, draft_id, RECEIPT, 'OK', _json(dict(identity_token=token, batch_id=batch_id,
                                                               request_id=request_id)))
                local_pending = bool(asset and status != 'CONSUMED' and
                                     get_row(conn, 'publisher_media_assets', asset['id'])['status'] == 'CONSUMED')
                if local_pending:
                    reason = 'LOCAL_CLEANUP_PENDING'
                _record_item(conn, request_id, batch_id, draft_id, status, reason, identity_token=token,
                             local_cleanup_pending=local_pending)
        results = {}
        for event in _events(conn, batch_id):
            if event['step'] == ITEM and event['payload']['request_id'] == request_id:
                results[event['draft_id']] = event['payload']
        counts = {key: sum(item['result'] == key for item in results.values())
                  for key in ('CONSUMED', 'ALREADY_CONSUMED', 'BLOCKED', 'FAILED', 'QUEUED', 'RUNNING')}
        pending = counts['QUEUED'] + counts['RUNNING']
        status = 'PENDING' if pending else 'PARTIAL' if counts['FAILED'] or counts['BLOCKED'] else 'COMPLETE'
        return dict(request_id=request_id, status=status, counts=counts, results=list(results.values()),
                    consumption=batch_consumption_review(conn, batch_id))
