# Headline H2: explicit local rendering

H2 does not integrate with worker uploads or initialize the operational database.
The additive `init_artifacts(conn)` migration is explicit and is used only in
isolated SQLite fixtures during this phase. `PublisherAPI.headline_renderer`
defaults to `None`: preview requests fail closed until an isolated caller injects
a configured renderer. H1/H2 must not be applied to `data/agent.db` in H2.

## Dependencies and executable resolution

`fonttools==4.66.1` is the sole new dependency: cmap, face parsing, outline bounds,
font metrics and extracting a selected collection face. No fonts are shipped.
Font references are an explicit caller-owned mapping to approved local files.
There is no font-name search, automatic Arial choice, download or fallback chain.

FFmpeg resolution: constructor explicit path; `ffmpeg_path` in
`config/media_tools.local.json` then `config/media_tools.json`; `FFMPEG_PATH`;
configured ffprobe sibling; finally PATH. Invalid explicit/configured paths fail
closed. Existing ffprobe resolution remains unchanged. Every chosen executable
is capability-validated; executable SHA-256 participates in cache identity.
Capabilities are cached in-process by binary hash, not merely executable path.
Tests validated FFmpeg 4.4.1-Jellyfin's installed drawtext options, including
`textfile`, `expansion=none` and `text_shaping=0`.

All processes use argv, `shell=False`, stdin disabled and finite timeouts. Output
is drained concurrently, bounded to 1 MiB normally / 8 MiB for decoded-frame metadata,
and 8 KiB stderr. Overflow of structured stdout fails closed. Errors expose a
stable code, never raw FFmpeg stderr (which can contain paths or text). A timeout
kills only the directly owned renderer child, not browser or Publisher processes.
Limits are capped at 600 seconds. Large unsupported inputs fail rather than grow
unbounded memory or silently degrade validation.

## Layout and fonts

H1 snapshot readiness/hash is checked first. Font bytes must match SHA-256, face
index must exist, and OS/2 weight must match. Variable fonts, combining sequences,
RTL/complex shaping, non-square-pixel video and non-orthogonal rotation are
currently unsupported and fail closed. Missing glyphs, including emoji without
a cmap entry, fail with `HEADLINE_UNSUPPORTED_GLYPH`.

Metrics use font units scaled to the rounded pixel size. Conservative rounded
advances, positive kerning, outline overhangs and a two-pixel rasterization guard
bound the layout. Wrapping converts an existing space into a line break, retains
other whitespace and explicit newlines, and never splits a word or reduces size.
Max lines, width and complete safe-margin bounds are checked before FFmpeg.
Background padding, stroke and four-sigma shadow bounds are included. Blur uses
an RGBA shadow layer plus `gblur`, because this installed drawtext lacks a blur
option. Preview and MP4 use the same layout and filter builder.

Each wrapped line is a UTF-8 text file in a UUID-named private TEMP directory.
No text is embedded into the filter graph or filename. Drawtext expansion and
shaping are disabled. `%`, quotes, colon, semicolon, brackets, backslash and
precomposed Portuguese accents remain literal.

## Encoding and validation policy v1

Video: MP4, libx264, medium, CRF 18, yuv420p, one encoder thread, faststart.
CRF 18 is the quality-oriented single-generation overlay policy, deliberately
favoring visual retention over bitrate targeting; it is not a lossless claim.
Changing this policy requires a renderer version/cache identity change.
Audio: preserve each track, copy AAC to avoid another generation; otherwise
encode AAC at 192 kbit/s while preserving channels/sample rate. No audio is added
to silent sources. Unsupported conversion fails output validation.

FFmpeg autorotation normalizes orthogonal display rotation. Output rotation must
be zero and display dimensions must match the input's oriented frame. No fitting
crop, padding, resizing or arbitrary 30 fps is performed. `-vsync 0` and an explicit
source `-enc_time_base:v` preserve small VFR discontinuities rather than rounding
them to the encoder's default frame-rate grid. This policy changes renderer/cache
identity, not the frozen Draft render-spec hash.

Decoded presentation frames from `ffprobe -show_frames` are the cadence authority.
Integer `best_effort_timestamp` values are converted with each stream's time base
and normalized to the first frame using exact fractions. Counts must match and
every presentation instant must agree within the sum of the two time-base ticks,
capped at half the smallest frame interval. Packet boundaries and DTS order are
not frame evidence. Final presentation extent additionally permits at most one
terminal frame interval for MP4 edit-list trimming; frame count and all instants
remain strict. Lost/added frames, duplicate instants and timing drift fail closed.

Duration tolerance is one frame interval (larger input/output average period)
plus one AAC access unit (1024/sample_rate), allowing muxing/priming rounding.
This is additional to decoded-frame cadence validation. Output must have one H.264 video
stream, yuv420p, correct display dimensions, expected audio tracks and duration.
Full FFmpeg decode with `-xerror` rejects unreadable/truncated artifacts.

## Artifacts and preview

Cache key: Draft ID + source SHA-256 + H1 render-spec hash + renderer identity +
kind. Renderer identity includes encoder policy, preview policy, fontTools
version and FFmpeg/ffprobe binary hashes. `preview` and `video` rows are separate.
States are `GENERATING`, `READY`, `FAILED`, not new Draft lifecycle states.

Write `output.partial`, validate probe/full decode/cadence, verify source has not
changed, hash output, atomically rename within its directory, then commit READY.
Interrupted GENERATING rows and partial files are never reused. Invalid READY
files become FAILED/CACHE_INVALID and a new attempt is inserted; history is not
deleted. Reuse checks containment, SHA-256, probe, full decode and cadence again.
No cleanup is connected to media consumption or Drive lifecycle.

Preview selects the existing frame nearest min(duration/4, 1 second), earlier on
ties. Single-frame videos select their only frame. This avoids blindly choosing
frame zero but cannot promise a nonblack frame in an all-black source. PNG uses
the exact video layout, including effects, and has its own cache row.

`POST /api/publisher/drafts/<id>/headline-preview` is explicit, local-only, and
does not accept a source path from the browser. It uses the persisted local
Draft video path constrained to the renderer's configured input root. Output is
a bounded inline PNG, not a filesystem path. No render happens on Queue/Library
refresh. The UI reports unavailable local source/configuration without download.

Artifacts must be under OS TEMP and outside the configured source root, named
internally. H2 UI and real render tests use synthetic fixtures only. Worker source
selection, upload, runtime/auth, Drive and operational database remain untouched.

## Verification

Run `python -m unittest tests.test_publisher_headline_renderer
tests.test_publisher_headline_timing tests.test_publisher_headline_preview_browser -v`
(one command line). Tests include
real 9:16 audio/silent/short/rotation/VFR fixtures, Portuguese/special text, blurred
shadow, cache corruption, timeouts/nonzero exit, disabled/fail-closed behavior and
Chrome Stable UI with isolated SQLite. Full Publisher, Queue and complementary
Drive suites are separate gates. No OAuth or operational media is needed.

References: [fontTools TTFont](https://fonttools.readthedocs.io/en/latest/ttLib/ttFont.html),
[FFmpeg filters](https://ffmpeg.org/ffmpeg-filters.html#drawtext-1).
Installed `ffmpeg -h filter=drawtext` takes precedence over newer online options.
