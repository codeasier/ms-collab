# Artifact catalog and named filters

Python standard-library, storage-neutral metadata core. It does not load credentials,
read/write files, connect to storage, produce download links, or start a server.
The first migration slice generalizes quarterly grouping, virtual directories,
search and named filters from the existing artifact module.

## Try the synthetic example

From the repository root (Python 3.12+):

```python
from packages.artifacts import Artifact, ArtifactCatalog, Definition, Scope, Snapshot

scope = Scope('product-alpha', 'demo-storage', 'builds')
snapshot = Snapshot(scope, '2026-04-02T00:00:00Z', (
    Artifact('release/tool.zip', '2026-04-01T10:00:00Z', size=1024),
))
catalog = ArtifactCatalog([snapshot])
page = catalog.query(scope, '2026Q2', Definition(path='release/', files_only=True))
assert page['total'] == 1
```

`python3 -m unittest discover -s packages/artifacts/tests -v` exercises this core.

## Contract

- Scope is the complete `(workspace_id, connection_id, bucket_id)` triple. Bucket
  IDs are internal configured identifiers, not URLs or credentials. No wildcard,
  all-workspace fallback or implicit public-space aggregation exists.
- A `Snapshot` is an immutable **observed subset**, not proof of a complete or live
  bucket. Duplicate keys and artifact modification times later than `observed_at` are rejected. Omission does not
  imply deletion. Each scope accepts one caller-provided snapshot (max 10,000
  records); replacement and freshness policy belong to the future sync adapter.
- Quarter assignment uses `modified_at`, an explicit timezone-aware ISO timestamp.
  `offset_minutes` is a caller-specified fixed index offset; default UTC, never the
  server locale. This is not an IANA/DST timezone engine. Virtual directories are
  generated separately in each quarter, have no invented modification time, and
  never overwrite real markers in that quarter.
- Keys are literal storage-relative strings, not filesystem paths. Repeated `/`
  segments remain distinct; dot-segments, backslashes, absolute paths, URL-style `://` keys,
  controls and invalid Unicode are rejected. A directory prefix ends in `/`,
  avoiding accidental `a`/`ab` overlap. A future provider must reject or explicitly
  map unsupported keys, not silently normalize them into a different object.
- Queries return stable pagination (1..100 page size), directories first and full
  key tie-breaks. Sorts support name, size and modification instant. Missing times
  sort last within each file/directory kind. Missing quarters return an explicit
  `quarter_available: false` empty result without creating anything.
- Named filters (`SavedView`) are immutable values bound to an exact scope;
  mismatched or missing views fail, never silently broaden a query. Conditions use
  case-sensitive literal substrings with AND-before-OR semantics, including AND NOT.
  `%`, `_`, quotes and other text have no wildcard/SQL meaning. Path/time/file filters
  always apply outside the condition expression. Times include both endpoints.
- Checksum companions are ordinary metadata by default, not implicitly discarded.
  To hide them, use an explicit AND NOT filter. This is an intentional difference
  from the old provider-specific presentation.

## Security and integration boundaries

**This package does not authorize access.** The application must authenticate,
resolve current workspace membership, module capability and connection access
before every catalog/view operation. A caller can request any loaded scope; the
scope and `SavedView` objects are not credentials. A saved/shared filter never
confers read or download permission. Cross-space sharing, revocation, administrator
editing and view persistence are not implemented here.

Metadata is untrusted text; consuming UIs must render it as text, not HTML. Future
HTTP endpoints must validate and authorize before projecting counts or records,
and protect against stale scope responses. Snapshot size limits do not establish
production performance guarantees. No HTTP/UI, sync, database, provider adapter,
private-bucket download or production isolation is delivered by this package.

- [Migration evidence and compatibility](MIGRATION.md)
- [Tests](tests/README.md)
- [Project migration plan](../../docs/migration-plan.md)
