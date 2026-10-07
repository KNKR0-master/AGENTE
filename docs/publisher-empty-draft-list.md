# Complete empty Studio listings

The authenticated Studio content page was inspected read-only, without invoking
Draft #39 verification. It displayed `Rascunhos 0` and a canonical empty state.
The empty state omits the `Todos os computadores` control required by the old
verifier. Its table also retains a header with the same `DraftRowLayout_FlexRow`
marker as media records. Those two assumptions prevented complete-empty results.

The observed empty container is `components_DraftTable_FlexColumn`, with two
direct `components_DraftTable_TUXText` children for the empty title and guidance.
Detection combines that structure, explicit zero tab count, no media rows, no
visible loading/error/pagination, and the existing authenticated-account guard.
The known table header is excluded; unidentified/malformed rows remain blocking.
No full DOM, tokens, cookie values, or query strings enter evidence.

Completeness and current match are separate from historical remote existence.
A complete empty observation records COMPLETE, count 0, NO_CURRENT_MATCH and
REMOTE_STATE_UNKNOWN with no listing error. No save checkpoint or duration is
needed to observe zero; strong nonempty matching still requires both.
Complete nonempty no-match results retain complete coverage, not partial coverage.

The Queue and Resolver expose the observation only while evidence is current.
The local safety summary uses upload status and remote-effect checkpoints;
unknown or started transfer retains duplicate risk. Even explicit NOT_STARTED
does not authorize automatic release or retry. Existing backend confirmation,
strong-evidence, acknowledgement and lifecycle guards remain authoritative.

## Draft 39 local audit (read-only)

- Batch 9, execution 10 item FAILED; MediaAsset 856.
- Upload status TRANSFER_IN_PROGRESS, processing PROCESSING, persistence UNCONFIRMED.
- UPLOAD_STARTED checkpoint 753, 2026-10-06T22:51:19+00:00, immediately before
  the code's single set_input_files call. This proves intent/entry, not completion.
- TRANSFER_RECONCILIATION PENDING checkpoint 754; no transfer-confirmed,
  media-ready, caption/settings-complete or save/persistence checkpoint.
- Transfer outcome is unknown in meaning; the stored upload enum is not
  TRANSFER_UNKNOWN. The current empty list cannot remove that uncertainty.

No operational Draft, media, resolution or evidence was changed by this fix.
The next real verification remains a manual user action after official restart.
