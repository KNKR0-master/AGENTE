# Live draft-list hydration probe

The pre-fix read-only probe reused the official Chrome endpoint through
BrowserRuntimeManager and the production parser. It did not create Draft evidence.
The correct account was authenticated. The observations after navigation were:

| Elapsed | Draft tab | Count | Empty structure | Complete |
| --- | --- | --- | --- | --- |
| 2078 ms | absent | unknown | absent | false |
| 2906 ms | Rascunhos 0 | 0 | absent | false |
| 3625 ms, after one tab selection | Rascunhos 0 | 0 | present | true |
| 4640-12796 ms | Rascunhos 0 | 0 | present | true |

Root cause: HYDRATION_TIMING_BUG. The prior one-shot read ran before the client
rendered the tabs; therefore it never selected Drafts. This was not an account
failure or an empty-row selector mismatch. The production parser already
recognized the real empty view after hydration and excluded its table header.

`read_hydrated_listing` now performs one navigation, up to 17 samples at 500 ms,
and at most one read-only Draft tab selection after it becomes available. Two
consecutive complete observations are required. Loading, errors, challenges,
login redirects, missing tabs and header-only tables cannot become complete
merely by reaching the time limit. There is no upload or navigation retry.

The observed empty structure has two direct DraftTable text children, without
DraftRow descendants. That structure plus explicit Draft count zero is primary;
localized empty text is only a diagnostic category. Unknown markup fails closed.

The explicit local POST `/api/publisher/inspect-current-draft-listing` takes
`account_id` and `human_confirmed: true`, not a Draft ID. It requires an existing
worker runtime and rejects active executions. The command runs on the official
Playwright owner thread, calls the same verifier reader, and returns only flags,
counts, recognized tab labels, account identity and a query-free page identity.
It never records reconciliation evidence, starts a runtime or releases media.

Draft 39 remains archived by its prior human decision. MediaAsset 856 remains
available, with persistence unconfirmed. A current empty list is not proof of
historical absence and never authorizes automatic release or retry.
