# Headline upload preparation (H3a)

`upload_preparation.prepare_upload_source` is the single source-selection boundary.
It returns an immutable `PreparedUploadSource`. Disabled/legacy snapshots use the
original JIT file without instantiating FFmpeg. Enabled snapshots require an H2
READY video artifact; preview images and original fallback are forbidden.

## Configuration

The Publisher worker lazily reads `config/headline_renderer.json` only for an
enabled Draft. Its `fonts` object maps snapshot font references to trusted local
font paths. Optional `ffmpeg_path` and `timeout_seconds` use the H2 resolver and
bounded process policy. Missing configuration blocks preparation locally. H3a
does not install fonts, create operational configuration or enable a canary.

Artifacts live below the OS temporary directory at
`PublisherDesk/headline_artifacts/<project-root-hash>/<artifact-id>`.
The original remains under the existing JIT cache. No derived file goes to Drive.

## Identity and upload boundary

The immutable `publisher_upload_sources` ledger binds Draft/execution, source
SHA-256, snapshot render hash, artifact ID, output SHA-256 and output probe.
The Draft points to its validated preparation record. Paths are not durable
identity. Product edits never affect the frozen Draft snapshot.

Containment, hashes, probe, decode and packet cadence are revalidated before
handoff and again immediately before the existing AccountSafetyGuard mutation
reservation. `UPLOAD_STARTED` remains immediately before file selection, never
before rendering. Local preparation does not consume mutation budget.

Preparation failure uses the existing manual-action blocker. Before upload,
the existing restoration path leaves the Draft retryable and pauses the
sequential execution; unstarted tail items are not silently skipped. Once upload
has possibly started, the existing idempotency guard forbids rerender/reupload.

## Cancellation and retention

Cancellation is polled during child execution and between validation stages.
Only the owned renderer child is stopped. Interrupted artifacts are not READY.
On a safe pre-upload attempt, stale video GENERATING rows are marked FAILED;
partial output is never trusted. This relies on the existing serialized worker.

Ambiguous transfer retains original and derived files and their exact identity.
After persistence and normal remote consumption, local cleanup removes only
contained artifact directories. Artifact and upload-source audit rows remain.
Human abandonment does not automatically delete artifacts in H3a; future local
garbage collection may consider explicitly abandoned items, without remote writes.

## Reconciliation

Derived uploads use their durable output probe for expected duration. Existing
caption/time/duration matching is unchanged. The verifier currently has no local
thumbnail comparison, so none is invented here. The retained derived video is
the visual authority if a future matcher uses a local thumbnail, not the original.
Readonly transfer inspection uses the actual upload filename.

## Validation boundary

H3a exercises migrations only on fresh fixtures and isolated copies of legacy
and current pre-H1 databases. No operational Publisher startup, database
migration, TikTok upload, save, Drive mutation or H3b canary is part of this phase.
No second prewarm/preparation path or UI feature is introduced.

### H3a validation (2026-10-07)

- H1: 18 passed; H2: 41 passed; H3a: 21 passed (80 combined).
- Verifier and batch-review routing: 22 passed.
- Queue: 62 discovered, 61 passed, 1 opt-in skipped.
- Full Publisher: 588 discovered, 586 passed, 2 skipped, no failures/errors.
- Separate Drive inventory/profile complement: 27 passed.
- Python compilation, Publisher JavaScript syntax and whitespace checks passed.
- Both realistic migration copies preserved all existing values in Products,
  Drafts, Batches, MediaAssets, executions/items and canonical Drive files/folders.
- Operational `agent.db` SHA-256 stayed unchanged. No operational migration or
  real upload/save occurred. The operational renderer configuration remains a
  separate H3b prerequisite.
