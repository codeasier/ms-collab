# Current-authority stored pipeline scope discovery

This is a read-only directory of **already stored local snapshot resources**, not
upstream pipeline discovery or production migration. It adds no configuration,
grant, schema, provider, credential, refresh, trigger, retry, cancel, log, ACP or
execution path. It does not enable real login, identity changes or deployment.

## Contract and authority

`GET /api/workspaces/{workspace}/pipelines/scopes[?after=connection~pipeline]`
returns `{workspace_id, items: [{scope, snapshot_revision}], next_after}`.
`scope` is exactly `{workspace_id, connection_id, pipeline_id}`. There are no
counts, observations, names, origins, connection configuration or credentials.
Only one optional `after` parameter is accepted. Pages contain at most 25 items.

The route uses the existing authenticated SQLite transaction. The configured
unified host uses current cookies/sessions, not a bearer conversion; the separate
legacy adapter retains its existing explicit verifier. Current identity and
session validity are rechecked after lock acquisition and at the final guard.
Current membership and `pipelines.read` are required before the directory SELECT
and JSON parsing. Viewer, developer and admin members read the same workspace
snapshot set. Operator status alone grants no data access. Write-only capability
is insufficient. Disabled modules and suspended workspaces are unavailable;
archived workspaces remain readable. Shared/commons is not a private-space union.

Pipeline storage does **not** use Artifact's independent readers/refresher/
approver sets, candidates, published revisions or approval provenance. Resource
read is module-level authority. There is no per-snapshot owner pin: an explicit
valid new membership grants module read, but an identity A→B→A transition alone
does not revive old sessions or old membership authority. Such transitions remain
closed in production and are simulated only in test fixtures.

Only the exact adapter's `pipeline-` resource namespace is discoverable. A normal
snapshot key is `pipeline-` plus 64 lowercase hexadecimal characters: SHA256 of
the JSON connection/pipeline tuple. Ordinary non-prefix generic resources in the
same module are legal workspace-store resources, but are not pipeline snapshots
and are neither directory entries nor parsed here. **Any** malformed key inside
`pipeline-`, including uppercase or truncated hashes, fails the whole directory.
This is not a lenient filter that hides damaged snapshots.

The hash cannot be reversed and is not authority. After current module read, the
bounded envelope supplies a candidate scope. The directory verifies its exact
shape, workspace and recomputed resource key, then applies the complete shared
snapshot codec and future-observation check. Positive safe resource revision is
validated too. Every matched record is checked before any page is returned.
A valid `runs: []` snapshot is listed. An empty trusted bootstrap snapshot is not
persistently distinguishable from an observed empty history, so neither is
labelled refreshed, approved, complete or provider-verified.

Opening a selected tuple uses the existing authorized runs, detail, task and
duration APIs. A listed revision/cursor is an observation, never a credential or
an authorization cache. Existing task revision limits and snapshot consistency
checks are unchanged.

## Honest boundedness and failures

The extra directory SELECT uses the existing `(workspace_id,module_key,id)`
primary-key range `id >= 'pipeline-' AND id < 'pipeline.'`, with BINARY ordering,
within one workspace/module. It projects at most 257 rows, each key truncated to
74 bytes and each text to 10,001 bytes using `substr(CAST(... AS BLOB), ...)`.
Byte projections preserve embedded NUL rather than silently truncating a TEXT
value to a different valid key/envelope. Keys require ASCII and JSON requires
strict UTF-8 decoding, after byte-size checks. More than **256 namespaced
resources** gives a value-free `503 scope_directory_unavailable`, before parsing.
The range avoids scanning arbitrary non-prefix generic resources to find matches.
The directory validates at most **10,000 UTF-8 bytes per snapshot** and a **1 MiB
aggregate UTF-8 input budget**. Aggregate overflow gives the same capacity error.
The envelope is parsed twice at most (scope extraction, then the shared full
codec); this fixed extra work is not a wall-clock deadline.

**These are additional directory projection/parsing bounds, not end-to-end
storage bounds.** The existing SQLite policy loader has already loaded and
validated global resource objects, including their text, before the module
callback. This change does not make all database loading bounded, eliminate
its cross-workspace corruption/capacity side channels, or promise that no raw
text was read before authorization. SQLite loading, B-tree lookup, locking and
transaction time are not newly cancellable or hard-time-bounded here.

Malformed readable envelope, scope/hash mismatch or future observation yields
`503 pipeline_data_unavailable`, with no partial page or offending value. Invalid
resource state rejected by the existing loader yields `503 storage_unavailable`.
An authorization error is not translated into an empty successful directory.
A valid empty directory says only that no namespaced stored scopes exist here.

Items sort by `(connection_id,pipeline_id)`. `next_after` is only the last returned
visible tuple, and exists only if another page exists. It never contains a hash,
scan offset or hidden count. Every next-page request reauthorizes and revalidates
all records. A removed/absent cursor gives `409 scope_directory_changed`; loss of
workspace/module access preserves its normal denial. A newly corrupt record
fails with 503 rather than masquerading as a cursor or empty-list success. Pages
are not a stable multi-request snapshot. Restart explicitly; there are no retries.

## Product path and acceptance

Only the unified cookie Pipeline page adds a picker. **Discovery workspace** is
explicitly separate from manual exact-ID fields. Current workspace options load
after session verification; stored scopes require **Load current scopes**, and
runs require **Open selected scope**. The legacy standalone UI stays manual.
No scope is persisted in URLs/storage/bootstrap or auto-opened.

Workspace/selection changes, loading another directory page, manual edits,
logout, pagehide and Back restoration clear runs, coverage, durations, details
and task state. Epoch plus abort guards discard late responses even if transport
ignores abort. Repeated pending list/open/detail/task operations do not create
duplicate requests; old completions cannot unlock a newer request. The page does
not retry directory failures or cursor conflicts.

The existing `pipeline-cookie-ui` workflow retains its old tests, browser
journeys, six required screenshots, permissions, runner and timeout. It also runs
pipeline API regressions, the cookie/SQLite directory matrix, mounted/client
regressions and `pipeline-scopes.cjs`. The new browser journey starts through the
actual HTTPS cookie picker, reaches run/details/tasks/durations in two product
spaces plus commons with identical IDs, covers empty snapshot, manual edits,
ignored abort, repeat operations, cursor invalidation, logout/pagehide/Back and
current revocation. It asserts zero CI provider calls, XSS-as-text, 4.5:1 picker
contrast and 390px no overflow. Its exact additional screenshot allowlist is
`ms-collab-pipeline-scopes/{desktop,mobile,tasks}.png`.

All three pipeline browser journeys use `stopSyntheticFixture`. Cleanup must
succeed before a PASS marker is printed. Only fictional metadata, synthetic
identity exchange and temporary SQLite/HTTPS are exercised. These gates are not
real OBS/GitCode acceptance, live data migration or production readiness.
