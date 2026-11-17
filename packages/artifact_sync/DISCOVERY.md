# Current-authority artifact scope directories

This is read-only discovery of **already provisioned local index scopes**, not
provider bucket discovery, grant management, or a new permission model. The
configured host uses `IndexedArtifactApplication`, not the legacy resource/hash
catalog adapter. No generic resource endpoint, new business table, credential, scheduler,
real identity-provider protocol or production migration is added.

## API and authority

- `GET /api/workspaces/{workspace}/artifacts/scopes` returns
  `{workspace_id, items: [{scope, catalog_revision}], next_after}`. A scope is the
  existing exact `{workspace_id, connection_id, bucket_id}` tuple.
- `GET /api/workspaces/{workspace}/artifact-admin/scopes` returns the same
  envelope with `{scope, capabilities}` items. It is mounted only when existing
  host artifact administration is enabled. Capabilities come from **the same
  helper as exact-scope status**, including host `allow_refresh` and mandatory
  review before user-facing approval. It returns no pending flag or revision.
- Both run in the existing authenticated SQLite transaction, with current
  session/identity checks before and after reading. Current membership, workspace
  lifecycle and module access are checked **before scanning keys**. Reader
  discovery needs module read. Admin discovery needs active workspace admin and
  read or write: a write-only explicitly granted refresher remains discoverable
  when host refresh is enabled. Neither operator nor admin role implies grants.
- The reader uses the existing bound-policy authority, full publication codec,
  exact workspace/connection/bucket and prefix/offset validation, snapshot digest
  and current approval provenance. Valid empty publication is listed; absent or
  no-longer-current publication is not. It selects SQL `NULL` instead of the
  candidate column and never reads or decodes private candidate JSON.
- `catalog_revision` is the public `published_revision`, **never** the index
  revision. Staging a new candidate does not change the reader directory.
  Admin listing validates private data only after existing explicit management
  permission succeeds, as exact status already does. No keys, names, counts,
  candidate data, URLs, credential references or real bucket configuration from
  a denied scope are returned.
- Opening a selection uses the existing catalog/status/review/refresh/approval/
  byte APIs. A directory response or cursor is not reusable authority. It does
  not grant byte downloads or enumerate byte capabilities.

## Bounds, errors and pagination

A request selects at most **257 bounded key projections** in one workspace and
rejects more than **256 raw scopes**. It validates all these local rows before
returning a page, not only the displayed 25. There is an **8 MiB aggregate UTF-8
JSON input budget** per projection pass: policy text before parsing, then publication and
(for a granted administrator) candidate text before validation. Existing status
may validate a scope for three capabilities; repeated inputs are charged each
time. Codecs have a fixed bounded number of internal validation passes, not a
hard CPU-time deadline. Each discovery SQL JSON projection takes a **BLOB byte
prefix of 1 MiB plus one sentinel byte**, not a TEXT substring: embedded NUL and
trailing data must not disappear into an apparently valid JSON prefix. The
existing codecs require exact canonical equality with `dump(ensure_ascii=True)`;
every accepted record is therefore ASCII, so its 1 MiB character and byte limits
are identical. Raw key projections similarly take 129 bytes for the existing
128-character ASCII identifier limit, then validate the entire projected key.
Both use strict UTF-8 decoding, never replacement or ignored bytes. Malformed or
split multibyte sequences fail closed. Policy is decoded after module
authorization and before the explicit per-scope grant check;
publication/candidate byte projections are decoded only after the existing
explicit grant succeeds, and reader queries still select SQL `NULL` for candidate.
No business mutation or provider work is involved. The separate receipt-only
transaction now writes only auxiliary receipts and safe clocks; each successful
GET performs at most two bounded directory projections, never 32 receipt-driven
rescans. The existing global policy loader remains outside those budgets.

Every response has at most **25 currently authorized items**. `next_after` is
null or a canonical 48-character `dcr1_` opaque receipt; it is not a tuple or
hidden scan position. Current identity and common scoped authority, plus the
visible scopes' grant revisions/bound policies, are bound server-side. Reader
receipts additionally bind the actual current approval authority and membership
proof. Admin receipts use the shared exact-status capability evaluator, without
reader proof or private candidate/index/publication revisions in the fence.

Both actions separately bind the current **visible key set**. Visible additions
or removal require explicit reload; ordinary candidate/content changes and
same-authority re-publication do not. Normal still-hidden scope changes do not
add an invalidation signal. Reader SQL NULL and delayed denied-payload decoding
remain mandatory. Full codec validation still precedes output.

Only the sole `after` query parameter continues a page. Old tuples, expired,
unknown, misbound or changed-fence tokens return the same value-free
`409 scope_directory_changed` after current identity/scope/module checks.
There is no fallback or automatic retry. The [receipt support matrix and
transaction contract](../directory_cursors/README.md) describes explicit schema
initialization, same-DB factory handle, 300-second fixed TTL, scoped quota and
clock retention. Existing unconfigured factories now return authorized 503 for
these directories, even with complete tables; exact hand-entered APIs stay
available under original authorization. Unmounted admin remains 404. Strict
service_runtime is not migrated and must not run the receipt initializer.

Bad keys, schema, policies or readable publication/candidate records fail with
`503 storage_unavailable`; capacity/input budget failures give
`503 scope_directory_unavailable`. Denied 403/404 records are omitted; unexpected
errors are not swallowed into an empty success. Workspace/module lifecycle 409
is preserved by both directory and exact administration status (older status
collapsed that case to 404). Empty success means only “no currently accessible
scopes”, never “no underlying resources”.

Capacity and corrupt-policy errors can depend on hidden provisioned records.
This is an explicit aggregate capacity/storage side channel, **not strict
non-interference**. No hidden names or exact totals are disclosed. This small
slice does not add a denormalized per-user index to remove that tradeoff. SQLite
lock/load/sort/transaction latency is not hard-cancellable or newly bounded by
this application-level record/input budget.

## Product entry and lifecycle

Both cookie-only artifact pages offer current workspaces, explicit **Load current
scopes**, selection and **Open selected scope**. The existing manual exact-ID
forms remain. No scope is automatically opened, persisted in browser storage,
placed in navigation URLs or copied into bootstrap. Shared workspaces are not
unions of private catalogs. Published empty catalog is distinct from no visible
scope, and request failure is not rendered as empty success.

Workspace/selection changes, new directory pages, manual scope edits, logout,
pagehide and restored pages clear the old results, review/confirmation and byte
selection using the existing controllers. Independent request generations fence
late responses even when AbortSignal is ignored. Repeated list clicks while
pending do not create duplicate requests. Failures and stale cursors never retry.

## Verification boundary

Cookie/SQLite projection regressions cover NUL-tailed canonical records, NUL key
aliases, invalid UTF-8 and split multibyte prefixes, denied private payloads and
the real 1 MiB/128-byte sentinel boundaries. They compare readable corrupt
records with the existing exact catalog/status/review failures; reader candidate
exclusion remains checked at the SQL projection boundary.

Existing `artifact-session-ui` CI runs the new cookie/SQLite matrix and real HTTPS
browser discovery journey, preserving all old gates and five screenshots plus
four explicit directory screenshots. Existing refresh and byte-download browser
journeys now exercise selection through discovery and byte-state clearing; their
original confirmation, CSRF, CAS, revocation, cleanup and screenshot gates remain.
Only synthetic provider/identity/metadata/byte fixtures are used. Real GitCode
login and identity-transition gates stay closed. No real OBS/GitCode acceptance,
production readiness, deployment or grant self-service is claimed.
