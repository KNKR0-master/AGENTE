# Headline H1: local configuration contract

H1 stores configuration only. It does not render frames, invoke FFmpeg, produce
derived videos, access Drive media, or change the worker/upload/cleanup path.
Original Drive files remain immutable. Caption and headline are independent.

## Storage and ownership

`publisher_products` owns the four editorial defaults: enabled, text, visual
preset ID, and required. Existing rows default to disabled, empty text, no preset,
not required. Nothing is inferred from names, captions, or Flow prompts.

The existing `publisher_presets` table remains the operational TikTok preset
(visibility/comments/account). Visual presets have a different lifecycle and
schema: `publisher_headline_presets` stores identity/name/active/current version;
`publisher_headline_preset_versions` stores append-only specs. Database triggers
reject updates/deletes of versions. Updating requires the expected current
version; archival is an explicit new version with `active=false`, not deletion.

Every new Draft captures `headline_snapshot_json` in the creation transaction:
enabled, required, exact text, full preset specification, preset identity/name/
version, provenance, render-spec version, and hash. All three creation paths
(remote references, Library, legacy local files) capture independently. Legacy
local duplication preserves the source snapshot. Editing a Draft's Product ID
does not silently replace its existing headline, just as it does not silently
replace caption. Explicit headline override is separate.

Product/preset edits never update existing snapshots. The explicit Draft override
uses the same `can_edit` guard as caption, inside `BEGIN IMMEDIATE`. Any execution
record, upload marker, ambiguous transfer, persistence, or frozen status blocks
the override. Required policy is copied at creation and cannot be disabled by an
override. Omitted preset in an override retains the exact captured version.
Each override creates a local `HEADLINE_OVERRIDE` audit event.

## Render spec v1

- UTF-8 text is retained exactly, including punctuation, accents, currency,
  percentages, emoji, and explicit LF line breaks. No silent truncation.
- Maximum 500 Unicode code points and 8 explicit lines. This bounds an editorial
  overlay's payload and layout work (not a caption); actual glyph fit, wrapping,
  font coverage, and rendering remain H2 responsibilities. Preset `max_lines`
  can impose a smaller limit, checked against explicit lines in H1.
- X/Y denote the text block's alignment anchor and top edge, relative to frame
  width/height. Alignment determines the horizontal anchor (left/center/right).
- Margins and maximum width use the corresponding frame axis. Font size,
  background padding, stroke, shadow offsets, and shadow blur use frame height.
  H2 must preserve these v1 units and enforce final glyph bounds inside margins.
- Colors are canonical uppercase opaque `#RRGGBB`. `null` disables background,
  stroke, or shadow. Wrapping is `word` or `explicit`. Font weight is a requested
  numeric weight; H2 must verify support rather than silently substitute a face.
- Canonical JSON uses sorted keys, compact separators, UTF-8, and finite numbers.
  Numeric spec fields are normalized before storage. The SHA-256 includes
  render-spec version, enabled, and (when enabled) exact text plus effective spec.
  Preset ID/name/version, font alias, provenance, policy, and timestamps are
  excluded. Parameters of disabled background/stroke/shadow effects are excluded
  too. Font bytes hash and face index remain included. Disabled rendering
  has a stable no-headline identity irrespective of dormant text/configuration.

No font assets exist in the project. H1 therefore uses a minimal font identity
contract: local reference slug, SHA-256 of intended font bytes, face index, and
requested weight. It stores no absolute Windows font path. The metadata is
operator supplied and structurally validated, not proof of asset availability
or licensing. H2 must resolve an approved local asset, verify its bytes against
the hash, and check face/weight/glyph support. It must never silently fall back to
a system font. No proprietary font is copied or committed by H1.

## Readiness and API

Missing required headline, enabled headline with empty text/missing preset,
invalid spec/hash/version, or excessive explicit lines block execution via the
existing `validate_fields` hook and Queue capability/preflight. Disabled optional
headline does not change readiness. This is configuration readiness only, not
rendering readiness. H1 must not be represented as delivering text on video.

- Existing Product save/read contracts include the four defaults.
- `GET /api/publisher/headline-presets`: visual presets/current versions.
- `POST /api/publisher/headline-presets`: create or append a version; archive via
  active=false. No hard-delete API, so referenced history cannot disappear.
- `GET /api/publisher/drafts/<id>/headline`: effective immutable snapshot.
- `POST /api/publisher/drafts/<id>/headline`: guarded local override of enabled,
  text, and/or preset ID. Required is not writable.

All HTTP paths are forwarded by the official dashboard server. Invalid
configuration uses the existing controlled JSON error contract. No endpoint
starts a worker, renders media, or performs remote actions.

## Migration and rollback

`headline_contract_v1` is additive and idempotent. Historical Drafts retain NULL
snapshots, interpreted as safely disabled; lifecycle, caption, and media states
are unchanged. Tests migrate an old-schema fixture and compare prior columns.
No operational DB was migrated as part of H1's fixture validation.

The prior stable code can ignore the additional columns/tables and read old
data. However, it cannot enforce the new required-headline policy. A rollback
must not execute newly headline-enabled drafts using old code. Additive schema
compatibility is not permission to bypass the new semantic contract.

## Validation scope

Dedicated tests cover migration, Unicode/limits, hashes, version conflicts,
snapshot independence, safe overrides, required preflight, multi-product Library
creation, official HTTP routing/errors, and a Chrome Stable smoke backed by a
temporary SQLite DB. No operational Product, Draft, Batch or media reservation
is used. H2 rendering, derived-artifact identity and worker integration remain
explicitly outside this change.
