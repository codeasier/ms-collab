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
This slice has no product-view UI or management/listing endpoint.

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

There is no database table, generic resource, file loader, per-user grant or
durable filter registry here. To change/remove a definition replace the trusted
factory and retire the previous instance; a running old process does not learn
a new configuration. A restored runtime does not recover these definitions by
itself. This is not a hot revocation service across replicas. **Index and identity
permissions are checked again on every request**, even when the client supplies
an observation from the previous page.

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
| `start_time`, `end_time` | Files mode only: independent inclusive bounds, date + uppercase T + hh:mm:ss, optional 1–6 fractional digits, uppercase Z or explicit ±hh:mm offset. An active time bound does not enable recursion. |

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
the complete validated descriptor, published_revision, and lowercase SHA-256 of
the full canonical ASCII publication envelope. It covers neither the private
candidate nor the request's temporary path/time/mode. Bind the full query
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
There are no shortcuts, share links or implicit recursive searches. Existing
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
