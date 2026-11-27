# Single-bucket product views (API v1)

An index reader can browse folders that lead to matching files, or search those
files, in one **approved, stored** quarterly inventory. A product view is an
optional server-side filter. It is not product ownership, permission to read
metadata or bytes, evidence of a complete bucket, or a live provider check.

## Opt in at a trusted factory

Only `IndexedArtifactApplication(..., product_views=definitions)` and the cookie
`ApplicationHost(..., artifact_product_views=definitions)` with its artifacts
module mounted support this endpoint. No configuration or an empty list/tuple
leaves it unmounted. The generic resource-backed `ArtifactApplication`, normal
demo, runtime manifest, and old named views do not gain product-view behavior.
A host with the Cookie application UI can also serve the browser described below.
There is no management endpoint or global listing for these definitions.

Supply a list or tuple of at most 128 exact descriptors. The registry copies and
validates the entire input; duplicate (workspace, connection, bucket, view ID)
keys and unsupported formats or fields fail construction. The following is
**synthetic composition**, not a default mapping or an instruction to provision
an account. Here `approved_parent` is the specific bound index policy record
already reviewed at fixture setup, not a lookup performed while serving a read.

```python
from packages.artifact_sync import IndexedArtifactApplication
from packages.artifact_sync.records import digest

scope = {"workspace_id": "lab", "connection_id": "idx", "bucket_id": "internal-01"}
definition = {
    "format": 1, "kind": "product-view", "match": "relative-key-ascii-substring-v1",
    "scope": scope, "view_id": "analyzer", "product_token": "Kit_",
    "discovery_label": "Analyzer",
    "parent": {"grant_revision": approved_parent.grant_revision,
               "policy_digest": digest(approved_parent.policy_raw)},
}
api = IndexedArtifactApplication(product_views=[definition], **configured_index_arguments)
```

Supply both the positive safe-integer grant revision and lowercase SHA-256 of
the exact canonical bound policy. An internal bucket ID, publication revision
or grant counter alone does not identify a provider or binding. A regrant that
keeps the same readers still invalidates the old parent pin. Do **not** capture
a replacement automatically. A new legitimate publication under the unchanged
parent does not require replacing the filter. The product token is an ASCII
alphanumeric followed by up to 63 ASCII alphanumerics, periods, underscores or
hyphens. Scope IDs and view IDs use the existing 1–128 character route grammar.
`discovery_label` is optional, trusted display text of 1–80 printable
characters with no leading/trailing whitespace. Omit it for an exact-ID-only
view. Labels are shown to current bucket readers; do not include private
configuration or credentials.

The product-view definitions themselves have no database table, generic resource,
file loader, per-user grant or durable registry. Optional saved searches below
store filters within a published view; they never create or modify that view. To change/remove a definition replace the trusted
factory and retire the previous instance; a running old process does not learn
a new configuration. A restored runtime does not recover these definitions by
itself. This is not a hot revocation service across replicas. **Index and identity
permissions are checked again on every request**, even when the client supplies
an observation from the previous page.

## Find explicitly listed views in a published scope

GET `/api/workspaces/lab/artifacts/idx/internal-01/product-views`
without query parameters. Only a current reader of that exact scope can
receive a list, and a publication is still required. The response contains
`format:1`, `kind:"product-view-choices"`, `scope`, `catalog_revision`,
`views` (only `view_id` and `label`) and `quarters`. Only definitions
explicitly labeled and still matching the current parent grant revision and
policy digest are listed. Quarters are from that same approved bucket
publication, not guaranteed to contain files matched by every view. If no
view is listed both arrays are empty. No product matching token, policy digest,
source endpoint, provider object, candidate or byte availability is returned.
A listed option and its catalog revision are not reusable authority.

## Read the tree, then refine

POST to `/api/workspaces/lab/artifacts/idx/internal-01/product-views/analyzer`
with a bounded UTF-8 JSON object and no URL parameters. The example scope is
synthetic. Bearer-index verification or the host's same-origin HTTPS session +
fresh CSRF proof still applies. This POST is a read. For the first page:

```json
{"format":1,"quarter":"2026Q2","page_size":10}
```

The root is *inside the selected bucket*. To drill into a returned directory,
use the same endpoint with its exact slash-terminated relative key as `path`;
send the `observation` from the first response as `if_observation`. For a file
listing at that path, explicitly select files mode. For example, a new read:

```json
{"format":1,"quarter":"2026Q2","path":"builds/","mode":"files","recursive":false,"start_time":"2026-04-01T00:00:00+08:00"}
```

| Field | Contract |
| --- | --- |
| `format`, `quarter` | Required: integer 1 and an explicit YYYYQ1–YYYYQ4. The stored index's fixed offset assigns quarters; no cross-quarter or latest-quarter fallback. |
| `mode`, `path` | Default `tree` and empty root. Literal directory prefix (trailing slash), max 1024 UTF-8 bytes and 32 segments; internal empty segments stay distinct. No URL decoding, SQL wildcard or filesystem normalization. |
| `page`, `page_size` | Positive integers, up to 1,000,000 and 100; defaults 1 and 50. Page past end returns no items, unchanged total. |
| `if_observation` | Lowercase 64-hex equality hint; required when page > 1. Use also on follow-up drill/filter reads. A stale hint returns 412 after authorization, with no previous/current results. Start a new read explicitly. |
| `recursive`, `keyword` | Files mode only: strict boolean default false and optional nonblank case-sensitive literal relative-key substring (1–256 Unicode code points; ASCII C0 controls and DEL are not allowed). Leading/trailing spaces in a nonblank keyword are data. |
| `conditions` | Files mode only. An ordered OBS advanced-condition list instead of `keyword`; see below. |
| `start_time`, `end_time` | Files mode only: independent inclusive bounds, date + uppercase T + hh:mm:ss, optional 1–6 fractional digits, uppercase Z or explicit ±hh:mm offset. An active time bound does not enable recursion. |
| `relative_range`, `time_zone` | Files mode only: provide both instead of absolute bounds. Use `today`, `week`, `month` or `30d` with an explicit IANA region (e.g. `Asia/Singapore`) or `UTC`. |

Unknown/duplicate fields, null in place of an optional value, unzoned/reversed
times, invalid numbers, a mode-inappropriate field (even false) and page > 1
without its observation are rejected. Content-Length is required, with a 16 KiB
body maximum. Do not put paths or tokens into URL parameters or send a product
token/alternate scope, user, sort order, saved definition, provider URL or
advanced boolean expression in a request.

### What counts as a result?

1. Select the approved snapshot and quarter in the exact authorized scope.
   Ignore directory records and files whose names end in .sha256 (ASCII
   case-insensitive). A name ending in .sha256.txt is not a checksum sidecar.
2. For the mandatory product substring, fold ASCII A–Z only and inspect the
   **stored relative key**, including any stored prefix; do not add a URI or
   search the provider bucket, internal bucket ID, or workspace. Underscore is
   literal. A file with the token only in its basename can match. A non-product
   file whose key contains the token can also match.
3. Tree mode projects just the direct child segment or direct file of each
   matching leaf. Folders are virtual, with size zero and no modification time;
   no marker or empty branch is exposed as evidence. Both a file named kit_x
   and a matching directory kit_x/ remain reachable. In files mode the current
   parent must match exactly unless recursion is explicit; all refinements are
   AND-ed with the mandatory filter. Sort directories first, then by literal
   (basename, full key). Deduplicate projected directories before counting and
   paging. Rows and counts therefore describe this level or this file query,
   not a prefix of the unfiltered catalog. No synthetic row ID is a capability.

Successful data has exactly: format=1, kind=product-view, match semantic, scope,
view_id, quarter, path, mode, observed_at, quarter_available, catalog_revision,
observation, page, page_size, total, items and source. Each item has key, name,
parent, modified_at, size, is_dir and virtual. `source` identifies a stored
index and makes no live-provider, full-history or download-availability claim
(all three corresponding booleans false). An authorized absent quarter has
quarter_available=false and zero results. A quarter with only markers, sidecars
or no matching leaves is present but may have zero results. An inaccessible
scope/view/publication is **not** a successful empty catalog.

The observation is SHA-256 of compact, ASCII-escaped JSON with sorted keys and
no insignificant whitespace: the array containing "product-observation-v1",
the validated read descriptor (without the optional discovery label),
published_revision, and lowercase SHA-256 of
the full canonical ASCII publication envelope. It covers neither the private
candidate nor an ordinary request's path, absolute time or mode. For relative
requests the array additionally ends with `"product-window-v1"`, range name,
zone key, and resolved UTC bounds (object keys `start_time` and `end_time`, ISO
seconds with +00:00). Begin a changed window as a fresh read without an old
observation. Once a window moves, a previous observation on a page or a save
returns 412, even if the publication is unchanged. Bind the full query
coordinates in clients separately. The value only compares content; it does not
establish identity, distinguish byte-identical restores, pin history, or grant
future access. catalog_revision refers to the published observation, not a new
private candidate. Rejected/unknown views do not fall back to a broad query.

### Compatibility and limits

This is **not full OBS equivalence**. Compared with the behavior reviewed at
[OBS #343](https://github.com/codeasier/msinsight-obs-web-browser/pull/343),
[#344](https://github.com/codeasier/msinsight-obs-web-browser/pull/344) and
[#345](https://github.com/codeasier/msinsight-obs-web-browser/pull/345), no match
can come from a provider bucket name, no aggregate bucket root is shown, a
same-named file does not hide a folder, and time input never assumes +08:00.
There are no API shortcuts, share links or implicit recursive searches. Existing
`SavedView`, `Definition`, `GET /views/{id}` and ordinary `POST /query` keep their
own behavior, including legacy sort/condition rules, markers and checksum rows.

The indexed snapshot boundary is at most 500 objects, 1024 UTF-8 bytes / 32
segments per key, one MiB of canonical snapshot JSON and existing publication
limits. This is not a total database memory budget or performance guarantee.
Only current readers of a current approval can see the projected rows, in one
authenticated policy transaction. Already returned metadata cannot be withdrawn.
A bound metadata read neither calls a provider nor grants availability/download,
refresh, administration, or sharing. Actual product ownership, recall/false
positives, source mappings, production data, deployments and live-provider
compatibility require independent decisions/evidence.

## Browse with a verified Cookie session

On an opted-in host, open **Artifacts → Open a product view**. This link is a
host capability, not evidence that your account or the current bucket has a
view. No configuration or an empty registry leaves the entry hidden and the
dedicated page and its two modules unmounted. Reload the document to pick up a
new host configuration; the hint is not a live configuration monitor.

The separate page at `/artifacts/product-views/` starts empty. Choose **Load
current workspaces**, select a workspace, a published bucket scope, a listed
product view and a published quarter, then **Open selected product view**.
Opening performs a fresh authorized exact metadata read. The picker uses the
existing explicitly configured scope directory. Unavailable discovery or
unlisted views can still use manual exact workspace, connection, **internal
bucket ID**, product view ID and quarter with **Open product view**.
Editing just the manual quarter and opening again reads that quarter’s root
under the same confirmed observation. Folder buttons follow verified children.
Breadcrumbs and **Up one folder** follow literal ancestors, including empty
segments; a file and a folder with the same name remain separate. Tree totals
count this level’s rows, not a product’s inventory.

**Apply file filters** returns only files at this location. The optional,
case-sensitive keyword is literal; spaces in a nonblank keyword count.
Start/end times are independently optional and inclusive. Use a real calendar
date, uppercase T, seconds, at most six fractional digits, and Z or a numeric
offset. Bounds do not select subfolders or change the quarter. **Last 7 UTC
dates (including today)** applies immediately, from 00:00:00Z six calendar
dates before today through today at 23:59:59.999999Z. The concrete bounds
remain visible and editable. This is not a rolling 168-hour window or a local
timezone shortcut. **Clear filters and browse folders** stays in the same
view, quarter and directory. Pagination offers 10, 20 or 50 rows per page.

Follow-up reads carry the publication observation; it is an equality hint, not
permission. If it changes (412), the old results, location, counts and evidence
are removed. **Reopen product view** explicitly reads a fresh root. An absent
quarter is different from a present quarter without matches or an inaccessible
view. An unverified or denied response never becomes an empty list, a broader
catalog or an automatic retry. Historical timestamps outside the browser’s
strict timestamp representation cannot be verified on this page. This does not
diagnose bad stored data. Neither the private product token nor the quarterly
index offset is supplied to the browser to recompute the server’s match.

The explicit picker uses existing workspace and bounded scope directory GETs
and one exact bucket choice GET. The product POST carries the exact scope and
view IDs in its API path; file directory, query, time and observation are sent
only in its body. No shared link, page URL, history state or browser storage
carries your selection. The page does not query another catalog or use a
provider to list product files. By default it has no file actions. Return to
the ordinary catalog explicitly and open a new scope there if needed. Recheck, sign-out and leaving the
document clear private page state; Back does not reopen a query. Other tabs
keep separate unsaved selections and recheck shared Cookie authority on their
next request. Late results cannot replace a newer selection or expire its
session; a current 401 clears the page and requires explicit re-verification.
Previously viewed metadata cannot be recalled.

### Optional: check a product file for byte access

A trusted Cookie UI can separately enable `ApplicationUI(..., product_file_downloads=True)`
on a host with product views and the content-bound artifact download service.
Neither feature enables this handoff by itself. Without the separate bootstrap
hint `artifact-product-file-downloads`, the product page has no file-action
buttons or mounted download panel. Ordinary catalog downloads remain unchanged.

1. For a verified file of at most **5 MiB**, choose **Check byte access**.
   Browsing or selecting a folder never requests bytes. The page first rereads
   the same scope, view, quarter, folder, filters and page with its original
   `if_observation`. It checks the publication revision and time and the exact
   file row, including its key, size and modification time. A mismatch does
   not fall back to another publication, view or ordinary catalog.
2. Only after that check does the page submit the frozen scope, key and
   published revision to the **separate** byte-availability service. It checks
   the returned scope, revision, key and size before displaying the approved
   SHA-256. Neither metadata recheck nor availability check reads provider
   bytes. They are separate requests, **not one atomic authorization**: the
   product observation is not a byte grant and is never sent to the byte
   service. `source.download_available: false` in the product API still means
   that its metadata response does not establish byte availability.
3. Review the exact key, size and digest, then choose **Confirm download**.
   Only that action requests bytes; the existing download service checks current
   authority and the content binding again. The browser hands off the file only
   after verifying the complete bounded length and SHA-256. The existing safe
   wheel naming rule applies only to eligible verified `.whl` basenames; other
   files use a sanitized suggestion. This does not install or inspect a package,
   create a permanent link, or prove that your browser saved the file.

Another file check, an edit, paging, a folder or quarter change, session recheck,
sign-out or leaving the document retires the local handoff. Following a fixed
application link in the same tab (including Return, Overview, Workspaces or
the brand) retires it immediately; opening a link in another tab does not. Late replies cannot be confirmed in its place. Canceling does not
recall bytes already delivered. Nothing is automatically retried or stored in
the page URL or browser storage. Synthetic coverage is not live provider or
production download evidence.

## Optional: save the current file query and reopen it by exact preset ID

This backend slice lets a workspace administrator save a file filter for a
published product view. A second authorized bucket reader who knows the preset
ID can re-run the same query against the current publication in that quarter.
The browser supports named absolute and relative queries. The compound
condition list below is an API contract, not yet an editable browser control.

Opt in twice: explicitly call
`packages.artifact_sync.initialize_product_presets(workspace_store)` in trusted
setup, then pass `artifact_product_presets=True` and product views to
`ApplicationHost` (or `product_presets=True` at the indexed API factory).
The default is off; a request never creates or repairs storage. The private
SQLite table uses the existing identity/policy transaction, not generic
resources. No saved result rows, download grants or provider access are stored.

At the exact product-view endpoint append, for example,
`/quarters/2026Q2/presets/linux-kit`. Use a non-sensitive route ID. POST the
active files query with its matching `if_observation`, removing transient
`page` and `page_size`. The request's quarter must equal the selected URL
quarter. The admin must also hold current artifact module write and the exact
index read grant. Duplicate preset IDs in the same binding are refused (409);
a stale observation returns 412. This slice is create-only, max 128 saved
presets per workspace/connection/bucket including retired bindings.

GET that same URL to re-run the saved filter. Both operations return the saved
`query` (no observation or pagination), exact `preset_id`, and `result` from
the ordinary authorized product projection. Absence of the selected published
quarter, changed product filter or grant binding, and loss of access do not
fall back to an unfiltered catalog. A new publication under the same grant
uses current rows, not frozen results. Presets retain the existing literal
keyword, directory/recursion and explicit absolute time semantics. There is no
global list, arbitrary search expression, update/delete,
or byte authorization in this slice. Separate browser, real deployment and
end-user replacement acceptance remain outstanding.

### Saving a moving window, not a pair of fixed dates

For an active file filter, use for example
`{"format":1,"quarter":"2026Q4","mode":"files","path":"pkg/","keyword":"linux","recursive":true,"relative_range":"30d","time_zone":"Asia/Singapore"}`.
Read it at the product endpoint, then POST the same query with **its returned**
`if_observation` to the chosen preset URL. Only the range and zone are saved
alongside the ordinary constraints: no calculation date or result list is
stored. On every authenticated active read, save or reopen the server reads
its trusted clock once for the calculation. The zone stays the same for
other authorized members. No URL, request clock or machine-local TZ fallback.
The host needs IANA tzdata for the selected zone.

The definitions follow [OBS #353's quick-range implementation](https://github.com/codeasier/msinsight-obs-web-browser/blob/519c1b82bc809c6d883faa96a6a5c88a0d4d5614/frontend/src/modules/artifacts/model/artifact-time-range.js).
In the saved zone, the start is inclusive local midnight on:

| Range | Start date |
| --- | --- |
| `today` | The current local date. |
| `month` | The first of the current local month, **not** the last 30 days. |
| `week` | The local date of the instant that was 7 × 24 hours ago. |
| `30d` | The local date of the instant that was 30 × 24 hours ago. |

All four end at **23:59:59 inclusive** on the current local date (the OBS
filter's precision is seconds; a later fractional timestamp is outside).
At a steady offset, `week` and `30d` can span eight and 31 date labels
respectively. Across DST, elapsed subtraction *before* selecting the local
date matters. The index's existing quarter assignment, bound product filter
and current grants still apply independently; a moving range never jumps
quarters. Absolute bounds, including microseconds, are unchanged. This
does not convert an old fixed preset or the browser’s **Last 7 UTC dates**
shortcut into a relative range.

### Reusing OBS advanced conditions

When a kit owner wants the Linux build but not the Jupyter variant, send this
file filter to the existing scoped product endpoint:

```json
{"format":1,"quarter":"2026Q4","mode":"files","path":"pkg/","recursive":true,"conditions":[{"operator":"包含","keyword":"kit"},{"operator":"AND","keyword":"linux"},{"operator":"AND NOT","keyword":"jupyter"}]}
```

The list uses the [OBS advanced search request](https://github.com/codeasier/msinsight-obs-web-browser/blob/519c1b82bc809c6d883faa96a6a5c88a0d4d5614/frontend/src/modules/artifacts/model/search-request.js)
and [ordered SQL conditions](https://github.com/codeasier/msinsight-obs-web-browser/blob/519c1b82bc809c6d883faa96a6a5c88a0d4d5614/backend/modules/artifacts/object_dao.py).
The first row is `包含`; each following row is `AND`, `OR`, or `AND NOT`.
AND and exclusion bind more tightly than OR: `a OR b AND NOT c` means
`a OR (b AND NOT c)`. An OR never skips the product requirement, current
grant, path, quarter or time bounds. Each trimmed term matches anywhere in
the visible **relative key**, not the hidden provider bucket name or URI.
As in SQLite LIKE, letter folding is ASCII-only, `%` matches zero or more
characters and `_` matches exactly one Unicode code point. Basic `keyword`
still uses the original case-sensitive literal match.

Use 1–8 flat rows each with exactly `operator` and `keyword`, at most 100
code points per keyword, no blank terms or control characters. Malformed or
nested conditions, an unknown operator, or a simultaneous basic keyword fail
with 400; they never turn into unfiltered results. Do not send empty UI
placeholder rows. The role, exact view/quarter and readback rules remain
unchanged. For a fixed compound query save the current `if_observation`.
For a rolling compound query also send the current `if_publication` from
the bound root's `observation` (returned as `publication_observation` on
the successful rolling read). Neither proof, the rows nor any byte grant is
part of the stored preset. Reopening requires current authorization and
recalculates a relative range using the trusted clock. This API does not
add a browser editor, a new download grant, or real-account acceptance.
