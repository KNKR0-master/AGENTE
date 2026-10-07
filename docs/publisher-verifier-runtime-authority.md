# Draft verifier runtime authority

Incident: Draft 39, Batch 9 (`lote06-10`), UNCONFIRMED. The user-triggered
verification reported AUTH_REQUIRED while the official Studio appeared signed in.
No new operational Draft verification was executed during this fix.

## Call chain and evidence

`dashboard/publisher.js` Verify action -> POST `/api/publisher/verify-reconciliation`
-> `PublisherAPI.post` -> `PublisherWorker.request_draft_verification`
-> worker command `verify-draft` -> `_verify_remote_draft_record`
-> `_inspect_draft_list_read_only` -> authority selection -> passive auth
classification -> read-only Draft list -> existing evidence append.

There is no separate PublisherService browser path here. Both upload and verifier
belong to the same PublisherWorker, whose BrowserRuntimeManager owns the CDP
attachment. Previously both called `context(playwright, account)`; the verifier
did not use an incognito context or a temporary profile.

Read-only official diagnostics during the incident returned AUTHENTICATED,
ACCOUNT_CONFIRMED, one page (`page-1`), host `www.tiktok.com`, path
`/tiktokstudio`, DOM collected and no visible login control. The official process
was PID 7372, parent server 18856, CDP localhost port 9222, profile ID
`a9a3acebc275495a8271b74e73faeab6` under LOCALAPPDATA/PublisherDesk/browser_profiles.
These are point-in-time identities, not fixed production configuration.

Confirmed implementation defects:

- More than one live page caused LISTING_ERROR without selecting Studio.
- A generic body-text phrase could cause AUTH_REQUIRED before canonical auth.
- Any result other than AUTHENTICATED, including UNKNOWN due to hydration or
  incomplete markers, became AUTH_REQUIRED.
- Authentication could select a page independently of the inspected target.

The old evidence did not distinguish those branches. Therefore the exact
historical AUTH_REQUIRED branch cannot be proven retrospectively. Multiple tabs
were not present in the later diagnostic and are not claimed as the incident's
proven cause. False-login classification paths are reproduced by isolated tests.

## Contract after correction

The verifier accepts only `worker.contexts[account_id]` matching the already
attached `BrowserRuntimeManager.session(profile).context`. It cannot launch a
browser, attach a second browser, create a BrowserContext or create a profile.
An unavailable/mismatched runtime returns an inconclusive inspection blocker.
Normal official startup remains responsible for runtime creation.

`select_verification_authority` examines live Studio tabs, preferring home, and
inspects each exact selected page using the existing classifier. A mismatched
account or challenge fails closed. An open editor blocks inspection. If no
suitable Studio authority exists, one page may navigate to Studio in that same
context. DOM hydration is observed at most five times over two seconds; there
is no navigation retry and no login action.

An unrelated/login first tab is not authority over another authenticated Studio
tab. A login URL or visible login UI is required for AUTH_REQUIRED; UNKNOWN stays
LISTING_ERROR. Captcha/challenge/2FA remain manual blockers. Account identity
continues to require one observed expected handle before reading Drafts.

Existing user tabs are never navigated or closed. The listing uses a temporary
page inside the existing persistent context, closed in finally. This is not a
temporary profile or incognito context. Context route guards are inherited and
their allowlist is unchanged. Only navigation/DOM reads occur; no editor actions,
uploads, saves, publishing or product linking are introduced.

Sanitized DRAFT_VERIFY_AUTH_OBSERVATION diagnostics record page identity,
hostname/category, auth result, account identity and blocker. No query strings,
cookie/token values or full DOM are logged by this event.

Upload runtime/auth behavior, resolution logic, evidence semantics, reservations,
cleanup and blind-retry protections remain unchanged. After offline validation
and a separate commit, restart through start-publisher.bat and hand back to the
user for exactly one manual Draft 39 Verify click. Do not resolve automatically.

## Offline validation

- Publisher core: 486 discovered, 484 passed, 2 opt-in skipped, zero errors/failures.
- New authority fixtures: 13 passed; existing read-only evidence tests also passed.
- Inspection/auth/runtime initialization and local workflow regression: 55 passed.
- Additional Queue regression: 41 discovered, 40 passed, 1 opt-in skipped.
- BrowserRuntimeManager: 6 passed. Drive inventory/profile complement: 27 passed.
- Python compile, JavaScript syntax and git diff whitespace checks passed.

The operational pre-restart read showed zero RUNNING/QUEUED executions. Draft 39
remained MANUAL_ACTION_REQUIRED/UNCONFIRMED, MediaAsset 856, checkpoint 753 and
the same old evidence ID 2 (AUTH_REQUIRED, REMOTE_STATE_UNKNOWN). No new verifier
attempt or human resolution was performed by the agent.
