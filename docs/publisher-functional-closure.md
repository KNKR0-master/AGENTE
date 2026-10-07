# Publisher: functional closure before visual redesign

Baseline: `d801714ba973af33c2beffdd52ba6b8b266e2773`.
Scope: Publisher only. Headline and offer recommendation below are designs, not implemented features.

## Ambiguous Draft decisions

The existing verifier remains read-only remotely and appends to
`publisher_reconciliation_evidence`. Eligibility now includes a failed item in a
stopped execution, in addition to WAITING_MANUAL. Running/queued execution or
processing/cleanup items still block resolution. Eligibility is checked again
when the worker actually performs the inspection.

The explicit local resolution endpoint supports three human decisions:

| Decision | Preconditions | Local result |
| --- | --- | --- |
| Found in Studio | Current, fresh, account-bound REMOTE_EXISTS_CONFIRMED evidence; same latest execution/checkpoint; existing transfer, media-ready, caption and settings checkpoints | Existing reconcile_received path sets READY_FOR_REVIEW/PERSISTED and keeps media RESERVED |
| Abandon and release | Explicit human confirmation, reason, duplicate-risk acknowledgement, matching current identity/checkpoint, stopped execution, no consumption or other reservation | Old Draft becomes ARCHIVED, media becomes AVAILABLE |
| Cannot confirm | Explicit human confirmation and reason | Audit only; ambiguous state and reservation preserved |

Finding a possible match manually does not fabricate strong evidence. If the
verifier cannot identify one match, the confirmation path stays closed and tells
the operator to verify again. Missing transfer/caption/settings checkpoints also
remain a blocker. A human may instead explicitly abandon the old Draft after
checking Studio and acknowledging the remote duplicate risk.

Abandonment is a deliberate narrow exception to generic archival. Generic reset,
remove and upload guards remain closed. The old Draft retains upload status,
remote checkpoint logs, evidence and TEMP reference. No Drive or TikTok object
is deleted. TEMP cleanup is not part of this decision. A new Batch may reserve
the released media only through the usual Library/inventory/decision checks;
remote unavailability or IGNORED still prevents normal eligibility.

The decision and state change share one SQLite transaction. The existing audit
log records human confirmation, time, reason, Draft/MediaAsset/account/execution,
remote checkpoint/evidence IDs, and previous/resulting states. A failed audit
write rolls back the decision. No new schema or lifecycle enum is introduced.

Execution rows remain historical truth. A manual outcome does not rewrite a
failed execution as successful and does not resume queued items. The batch
review derives current operational resolution from Draft and MediaAsset state.

## Batch review and local posting declarations

Batch names and current Draft counts lead the Queue. Execution IDs, execution
status and cleanup/cache counters are under technical disclosure. Progress is
explicitly preparation/resolution progress, not a claim that TikTok published
anything. Attention items are excluded from the successful preparation count.

Review lists every Draft, including unresolved and abandoned items. The operator
selects eligible videos and explicitly confirms that they were published manually.
The server revalidates membership, persistence, lifecycle, media state and running
execution for every submitted ID inside a transaction. It returns exact registered
and skipped IDs/reasons, plus remaining attention count. Repeated submission skips
already-published items. A concurrent lifecycle change invalidates eligibility.

The existing PUBLISHED state and transition are reused, with existing checkpoints
preserved. No worker command, remote publish, product binding, scheduling, cleanup
or Drive call is made by batch review. The individual registration path receives
the same persistence guards. Audit failure rolls back registration.

Batch hide/restore continues to use presentation state. Restoring a Batch does
not clear explicit Draft or Execution hides. Historical identity remains
`(execution_id, draft_id)`.

## Headline architecture proposal

Current facts, inspected locally on 2026-10-06:

- Publisher Product has default_caption and flow_prompt, but no headline contract.
- Preset currently holds account, visibility, comments and product_required.
- Draft snapshots caption; no headline snapshot or derived-artifact identity exists.
- `src/media_tools.py` resolves configured ffprobe, with environment/config/PATH precedence.
- Current ffprobe is 4.4.1-Jellyfin. Sibling ffmpeg.exe exists and reports the same version.
- Local ffmpeg reports drawtext, libx264 and AAC support.
- Windows lists Arial, Arial Bold and Segoe UI. Their presence does not grant redistribution rights.

Proposed ownership, requiring a later additive migration:

| Owner | Proposed data |
| --- | --- |
| Product | default_headline_text, optional explicit strategy, default visual preset reference |
| Visual preset | immutable version, font asset ID/hash/license, size, color, alignment, normalized position, margins, maximum lines, wrapping policy, optional background/stroke/shadow |
| Draft | copied headline text and complete preset snapshot, override provenance, required flag, render specification version/hash |
| Derived artifact | source identity/checksum, Draft ID, render-spec hash, encoder version, output probe/hash, local path and generation state |

Product/preset edits must never mutate existing Draft snapshots. Overrides are
editable only before remote effects, with the same safety boundary as caption.

Proposed preparation pipeline: existing JIT original in TEMP -> validate source
identity and ffprobe -> render derived TEMP -> probe output -> atomically mark
artifact ready -> existing upload adapter receives the derived path. Preserve the
original Drive file and original TEMP until safe lifecycle cleanup. Interrupted
render output is never a candidate for upload. Required-headline failure blocks
upload; no silent fallback to the original.

Resolve ffmpeg through configuration, not a hardcoded sibling assumption. Launch
with an argv list and shell=False, bounded timeout and bounded diagnostic output.
Use an explicit font file and validated preset values. Headline text should be a
UTF-8 text file, not interpolated filter commands; disable text expansion and
validate filter-path escaping. Test punctuation, percent signs, newlines and emoji.
Unsupported glyphs require a visible validation error or approved font fallback.

Recommend H.264/yuv420p MP4 with AAC where needed, preserving duration, rotation,
frame cadence and audio behavior after probing. Do not blindly stretch or crop.
For 9:16 define normalized, user-previewed safe margins; do not claim universal
TikTok safe-area constants. Test rotation metadata, VFR, absent audio, long text,
HDR/color metadata and output duration tolerance before production adoption.

Preview: representative source frame plus the exact same layout specification,
font and wrapping engine. Reuse a local JIT/cache frame if available; do not add
full downloads to ordinary Library list rendering. Cache preview by source and
render-spec hashes. Show boundaries and clipping before execution.

The official FFmpeg [drawtext documentation](https://ffmpeg.org/ffmpeg-filters.html#drawtext-1)
describes font files, text files, positioning and text expansion. Production must
probe the installed version because current online options may not exist in 4.4.1.

## Offer recommendation architecture proposal

No Analytics code or data is used in this discovery. Publisher currently exposes
internal Product ID/name/short_name, persisted product_aliases, legacy_source,
legacy_product_id and legacy_shop_url. Legacy references are clues, not a verified
TikTok Shop offer mapping. Publisher has no dedicated, versioned offer metrics
contract for seller, orders/GMV, CTR, creators, commission, price, stock or affiliate
eligibility. Product created/updated timestamps are not metric observation dates.

Before implementing ranking, define a read-only authorized source/import contract
owned by Publisher. Do not invent metrics, scrape Studio or depend on Analytics.
Candidate identity should include internal Product ID, exact external product and
variant identifiers, seller/shop identity, source and verification provenance.
Text similarity and aliases may suggest candidates for human review, never bind
an offer automatically. Reject mismatched quantity, size, variant or bundle.

Future metric snapshots need source, observed_at, measurement window, currency,
denominator and confidence for every metric. Missing values remain null. Distinct
creators must share the sales window; sales_per_creator is null when creators is
zero/unknown, and is only a descriptive efficiency proxy, not causal performance.
CTR needs comparable clicks/impressions and window; never rank missing CTR as zero.

Hard gates before scoring: verified identity, current stock/availability, valid
affiliate eligibility, and acceptable source freshness. Unknown gate values mean
human review, not eligibility. Then configurable weighted normalized components
can consider sales velocity, CTR, sales per creator, creator saturation, commission,
price and stock reliability. Store score version, contributing observations,
coverage and reasons; use minimum coverage and stable tie-breaking. Missing-data
renormalization must not reward sparse candidates.

Future UI: recommended offer plus two alternatives, seller, price, commission,
metrics with dates, identity confidence, missing-data warnings and a plain-language
explanation. Keep final TikTok product binding manual. This task adds neither a
recommendation engine nor automatic linking.

## Remaining sequence

Final isolated validation on 2026-10-06:

- Publisher discovery: 473 tests, 471 passed, 2 opt-in skipped, no failures/errors.
- Queue and new local-workflow targeted runs: 78 tests, 77 passed, 1 opt-in skipped.
- Separate Drive inventory/profile complement: 27 passed.
- Python compile, JavaScript syntax and git diff whitespace checks passed.
- Chrome Stable fixture verified desktop/mobile review, explicit resolution,
  refresh survival and local-only registration. Production runtime was not started.
- Historical FAILED execution filters remain available after local resolution;
  a persisted resolved Draft no longer receives an artificial attention filter.

No operational database mutation, real OAuth attempt, TikTok action or Drive
remote write was performed. Operational human validation is still a separate step.

Validate the new local workflows operationally before performance work. Headline
and recommendations require their own implementation gates. Refresh coordination,
N+1 reduction, DOM fingerprints, negative thumbnail caching and final Figma design
remain deferred. No merge, stable tag or push is authorized here.
