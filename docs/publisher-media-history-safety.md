# Media history safety and local failure recovery

Implementation at base `29d72b1459b0dd110e0cb68919b87dd795f78b63`.
Functional validation is pending. No operational database migration or data
repair is included; the operational cases are references, not test inputs.

## Historical identity

`media_history.py` checks the stored MediaAsset and its canonical
`(provider, source_account, remote_file_id)` identity. It reads every related
Draft, including ARCHIVED identities, their upload/processing/persistence flags,
remote checkpoints, mutation ledgers and reconciliation evidence. Historical
attempts retain their own Publisher account binding. A different TikTok account
does not erase risk for the same source file; different source identities and
other videos of the same Product are not conflated.
Library inventory joins and creation resolve the canonical Google Drive/Cecilia
asset, rather than selecting an asset by unscoped file ID.

Unconfirmed remote effects block reservation and upload with
`MEDIA_HISTORY_RECONCILIATION_REQUIRED`. Missing or conflicting identity fails
closed with an identity-specific code. `ABANDON`, `ARCHIVE` and a Studio listing
absence observation do not grant reuse. Human risk acknowledgement also does
not constitute a second-upload authorization.

Successful domain persistence/reconciliation retains normal review and explicit
consumption of the existing Draft. It does not grant a new Draft another upload
of the same video: existing consumption/availability rules and
`MEDIA_HISTORY_REUSE_NOT_AUTHORIZED` continue to protect that case. Local-only
cancelled history permits normal creation of a new Draft. No new reuse override
or authorization workflow is introduced.
Executed historical identities without remote markers still require audited
local cancellation proof; archive/reset flags alone cannot establish safety.

`assert_media_available` guards both creation services inside their existing
BEGIN IMMEDIATE transactions. `assert_upload_allowed` rechecks existing
reservations at enqueue, transitions and worker preparation. Immediately before
file selection, the worker passes that guard to `start_mutation`: identity proof
and mutation-ledger intent share the write transaction. Once committed, the
ledger protects against concurrent release even before UPLOAD_STARTED is logged.
The final guard also binds the cached prepared asset and browser account to the
current stored identity, so a stale source/account cannot silently pass.
The intent is conservative after a crash; it never enables automatic retry.

Queue capabilities and Library classification carry the historical blocking
Draft/account and a user-facing reason. ABANDON still closes its local
reservation transactionally and audits that reuse remains unauthorized.

## Local FAILED cancellation

The existing partial cancellation service now accepts the audited
`GOOGLE_USER_CREDENTIAL_REFRESH_FAILED` case only when the Draft is FAILED,
upload/processing are NOT_STARTED, persistence is UNCONFIRMED, no remote effect
or consumption exists, the reservation identity is consistent, no conflicting
reservation/active worker exists, and every associated execution item proves a
coherent stopped failure. The PAUSED execution must have a finish time and a
complete item count; its FAILED item and matching failure log must fall within
that execution's time window. Unknown failures remain blocked.

Historical risk for the same media, including an archived predecessor, blocks
this local release. Thus a Draft shaped like #52 can be cancelled; one shaped
like #51 with predecessor #49 cannot use its local failure to clear remote risk.

Cancellation preserves frozen snapshots, checklists and logs; records the prior
state/error in BATCH_CANCELLED; terminalizes eligible FAILED/WAITING_MANUAL items
as FAILED/LOCAL_BATCH_CANCELLED; and releases only the matching reservation.
An execution completes only when all its items were locally cancelled. Mixed
executions keep their existing stopped/review state. Repeated cancellation is
idempotent and uses the existing SQLite write lock.

The existing Queue/Hidden action reads “Cancelar trabalho local e liberar mídia”.
Library reservation navigation remains available. Hiding still changes only
presentation. No Drive deletion, upload, automatic retry or snapshot thaw is
introduced by cancellation.

## Prepared validation

`tests/test_publisher_media_history_safety.py` uses temporary SQLite and fake
remote adapters. It covers archived/abandoned history, legacy reserved successors,
ledger-only risk, account/identity separation, listing absence, successful
reconciliation versus second-upload authorization, missing identity, local
FAILED cancellation, ambiguous transfers, mixed executions, audit rollback,
idempotence, concurrent reservation/cancellation and the final intent write lock.
The existing manual-resolution regression now expects historical protection
after local release. These functional tests have not been executed in this task.

Changed files:

- `src/publisher/media_history.py`
- `src/publisher/service.py`
- `src/publisher/queue_policy.py`
- `src/publisher/account_safety.py`
- `src/publisher/worker.py`
- `src/publisher/library.py`
- `src/publisher/manual_resolution.py`
- `dashboard/publisher.js`
- `dashboard/publisher.html`
- `tests/test_publisher_media_history_safety.py`
- `tests/test_publisher_manual_resolution.py`
- `docs/publisher-media-history-safety.md`
