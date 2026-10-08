# Saved headline workflow (H2.5)

Headline is short visual text burned into the derived MP4 by H2/H3a, not the
TikTok caption or a Studio text sticker. No TikTok text editor is automated.
This phase uses isolated databases and synthetic local media only. It must not
start the operational Publisher or migrate `data/agent.db` before the H3b gate.

## Daily Configuration

The Presets view contains the saved headline library. A template has a name,
text, optional Product scope, active flag and sort order. Global templates are
available to every Product; scoped templates are restricted to their Product.
Search and active/inactive filters are local. Editing or deactivating a template
does not change a previously created Draft snapshot. Obvious duplicate names or
text in the same scope are rejected; identical text in different scopes is legal.

Product configuration stores a reusable suggestion: saved or custom text,
Classic style and controlled size. It is not authorization to render any video.
Every new video defaults to `none`, even when Product enabled/required legacy
fields are set. Existing Draft snapshots and their captured policies are not
rewritten. Caption remains independent.

The Library's inline batch panel has a collapsed headline section for each
selected video. Choices are none, Product default, global/Product saved text or
custom text. New templates can be created contextually. Bulk text, style and size
actions require confirmation showing text, style and selected video count.
Changing style/size alone does not enable a disabled video. Product suggestion
in a multi-Product selection resolves each
Product separately; it never copies one Product's text to another implicitly.
Headline choices survive unrelated refreshes. Batch creation validates and copies
each effective snapshot in the same reservation transaction.

Absent per-media choices are disabled in Library, remote-reference and local
Draft creation alike. Explicit `product_default`, `template` or `custom` choices
capture enabled snapshots; `none` captures a disabled snapshot. No migration,
historical repair, upload, resume or retry is performed by this change. H3a
continues selecting ORIGINAL for disabled snapshots and a validated derived
artifact for enabled snapshots. Draft #44 / Execution #11 remain untouched.

Editable Queue Drafts can choose or adjust a headline before freeze. Bulk Draft
edits reject the entire selection if any Draft is frozen. Queue details show a
compact headline/style summary. Historical snapshots retain their text, template
name and exact H1 preset version even when templates or defaults later change.

## Controlled Visual Styles

The initial family is `TIKTOK_CLASSIC`: white, yellow, black background and white
with black outline. Each of SMALL, STANDARD and LARGE is a separate H1 versioned
preset, not a mutation of a shared style. Style initialization is an explicit
local action after machine readiness passes and is idempotent.

These are product style names, not a claim to redistribute TikTok's proprietary
font. Only an approved configured font is used. The normal interface does not
show Windows font listings or font paths/hashes. The pre-existing advanced H1
preset editor remains behind a closed details section for compatibility.

The supplied presets use normalized top-safe positioning, bounded central width,
maximum two lines, a maximum block height and a conservative bottom bound. They
are guides, not universal TikTok UI coordinates. H2 performs exact glyph/word
wrapping and rejects overflow; there is no silent shrink, free positioning or
timeline UI. Text remains visible for the full video.

## Preview

Preview is explicit and uses the same H2 font, glyph, wrapping, layout and style
as the final render. It requires an already local source under the existing JIT
TEMP root. Opening Queue/Library or refreshing does not download or render media.
Missing local source/configuration produces a friendly blocker.
Editing text/style/size invalidates the displayed preview until the new Draft
snapshot is saved; a stale preview cannot be presented as the pending selection.

The preview's optional TikTok safe-area mask is an HTML overlay only. Its toggle
does not invoke the renderer, change a snapshot/render-spec hash or alter a PNG
or MP4 artifact. Desktop/mobile Chrome Stable tests verify those boundaries.

## Portable Renderer Configuration

`config/headline_renderer.example.json` documents the nonsecret contract.
The machine-local `config/headline_renderer.json` is ignored by Git and was not
created or enabled for the operational environment in this phase.

- `enabled`: explicitly enable the renderer after assets are configured.
- `ffmpeg_path` / `ffprobe_path`: optional explicit executable paths, otherwise
  the existing executable resolvers apply. Paths may be absolute, relative to
  the project, or contain environment variables. No username is hardcoded.
- `fonts`: logical asset mappings. Classic requires a path, approved SHA-256 and
  optional face index. Paths are not stored in Drafts; snapshots store logical
  reference, verified hash, face and weight.
- `classic_font_ref`: the approved mapping used to initialize Classic variants.
- `timeout_seconds`: the existing bounded H2 process timeout, default 120.
- `default_style`, `default_variant`, `default_size`: documented initial family
  defaults; each Product/Draft explicitly persists its chosen H1 style and size.

No font or binary is downloaded, bundled or silently substituted. Deployment
must provide approved assets and executables. Machine readiness verifies font
identity, FFmpeg capabilities/binary identity and executable ffprobe identity.
Enabled headline items block at Library/Queue preflight and before worker JIT
download when the machine is not ready. Disabled items retain the original
source path and do not require the headline renderer.

H3a's immutable upload-source ledger, ambiguous-transfer policy, persistence,
remote cleanup, auth, account safety and browser runtime are unchanged.

## Schema And Verification

`headline_workflow_v1` adds template and style-variant tables plus Product default
template/size fields. It is additive and idempotent. No existing Draft is rewritten.
The migration was exercised only in isolated SQLite databases/copies.

Verification includes saved/global/scoped/custom/none choices, immutable snapshots,
duplicate protection, twelve deterministic style/size specs, 720x1280 and
1080x1920 safe layouts, fail-closed overflow, bulk/freeze behavior, missing tools
and approved-font failures, independent Chrome Stable UI, H1/H2/H3a regressions,
Queue/verifier/review and the full Publisher plus complementary Drive suites.

H3b remains a separately authorized operational canary. This commit does not
start it, migrate operational state, write Drive or execute TikTok actions.
