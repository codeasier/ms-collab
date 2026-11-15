# Workspace research assets

A business catalog, **not** the binary artifact browser. This package supplies
stable asset identities, strict normalized projections, type/entity discovery,
filtered/paginated reads, and authorized multi-source atomic publication. It is
an independently implemented migration slice with synthetic verification, not a
production data import or a working source connector.

## Domain and stable identity

Four descriptors remain discoverable even with zero entries:

| Type | Identity within a scoped namespace/entity | Read projection |
| --- | --- | --- |
| `api` | Module + command | Request/response/errors/notes as bounded text |
| `records` | Explicit document ID | Titled text |
| `runbook` | Explicit runbook ID | Module/status and declared sections |
| `fault` | Library + explicit entry ID | Selected metadata, symptoms and guidance |

Asset IDs hash a versioned tuple of workspace, catalog, business namespace,
entity, type and business key. They do **not** depend on source aliases,
repositories, file paths, titles or source revisions. A move between configured
sources or a title change retains identity. Different workspaces/catalogs do not
share IDs. Duplicate business identities reject the entire batch; there is no
last-writer-wins behavior. Hash IDs are identifiers, not access tokens or a
secrecy guarantee against guesses about business names.

Unlike legacy title-based records, this boundary requires explicit record IDs.
It does not promise compatibility with old IDs or silently choose a filename or
content hash. A future migration must reconcile legacy identities explicitly.

`project()` accepts an exact normalized dictionary per type, not arbitrary
YAML/Markdown or source trees. Layout parsing is outside this package. Unknown
fields fail; source paths, repository coordinates, raw documents, fault evidence,
regular expressions and case statistics are not passthrough fields. API contract
sections are already-selected plain text, which may describe a logical `path`.
Text remains untrusted data; a future UI must escape it rather than render HTML.
This validator is **not** a secret scrubber. Trusted adapters must select content
approved for this catalog's audience, not everything their credentials can read.

A runbook's `verified` status or fault confidence is a source declaration.
Responses always say `execution_verified: false`. Nothing here executes steps,
validates a running product, diagnoses an incident or enables remediation.

## Trusted provisioning and authorized reads

The host provisions policy principals/workspaces/modules first, then calls
`provision_catalog(store, CatalogBinding(...))` explicitly. A binding fixes the
workspace/catalog namespace, display entities and complete required source set.
Each `SourceSpec` constrains permitted entity IDs and asset types. No defaults
name real organizations, repositories, directories, credentials or endpoints.

Provisioning the same binding is idempotent; changing/removing a source or other
binding data is rejected. There is no live reconfiguration route. Future source
retirement requires a separately reviewed migration, not silently omitting it
from a refresh. Provisioning does not grant membership/module capabilities.

`AssetApplication` requires the existing initialized `SQLiteWorkspaceService`
and a configured trusted identity verifier. It uses the same SQLite snapshot for
current subject binding, active membership/module authority and catalog reads.
The `research_assets` module must permit `read`. Shared workspaces still require
membership and never union private catalogs. Generic resource APIs cannot read
these dedicated tables.

GET routes are rooted at:
`/api/workspaces/{workspace_id}/research-assets/{catalog_id}`

- `/types`: descriptors and scoped counts.
- `/entities`: business entities and scoped counts.
- `/assets?type=&entity=&q=&page=1&page_size=50`: stable ordering by type/title/ID;
  default page size 50, maximum 100. Supply only nonempty optional filters.
- `/assets/{opaque_id}`: one asset's read-only sections.

All reads return scope, catalog revision, availability and explicit unverified
flags. Optional `revision=N` fences list paging, descriptors and details against
a stale view (412 on mismatch). `available: false` means never published, not a
successful empty source. A successful complete empty batch yields true and zero
assets. List SQL deliberately omits detail bodies. Unknown/duplicate query keys,
legacy source-tree coordinates, unknown types/entities and unbounded requests
are rejected. Responses are no-store; errors never echo raw source failures.
There are no HTTP writes, sync endpoint, listener, login factory or CORS setup.
A host must provide TLS, trusted WSGI/proxy normalization and request/concurrency
limits; this package does not make the synthetic demo production-ready.

## Multi-source publication

`AssetSynchronizer.refresh(user_credential=..., scope=..., expected_revision=N)`
is a **trusted host method**, not an HTTP endpoint or scheduler. The caller must
currently have `research_assets.read` and write access to a separately configured
`research_assets_sync` module. A viewer cannot synchronize.

The explicitly injected read-only provider implements `prepare(scope, source)`
and returns `SourceBatch(scope, source_id, revision, observed_at, documents,
complete=True)`. The completeness flag declares a full normalized projection of
that configured source, not proof of full repository/provider coverage. An
incomplete, failed or missing source must not be represented by an empty tuple.
There is **no** real Git/network/mirror reader, token discovery or provider write
implementation in this increment. A real provider needs its own reviewed
credential/destination binding, traversal/SSRF/budget controls and evidence of
protocol compatibility.

1. Verify the user assertion and current binding/permissions/revision.
2. Before each source preparation, repeat current authorization and the baseline
   check; detach/validate/bound its result before preparing another source.
3. Reject unexpected/duplicate sources, out-of-scope data, disallowed types or
   entities, future/malformed observations, oversized data or business conflicts.
4. In one write transaction, re-resolve the same actor, authorize again and CAS
   the catalog revision/configuration. Replace **all** source projections and
   advance catalog revision together with the existing policy audit.
5. An older observation, a changed source at the same observation time, or changed
   content claiming the same immutable source revision is rejected. Exact replay
   does not increase the revision or append another audit event.

Any failure preserves the previous catalog and audit. Concurrent refreshes have
one CAS winner, even across processes. Identity expiry during publication or the
commit guard rolls back everything. There is no database lock held while calling
the provider. A preparation already past its local authorization check may still
start/complete if access is subsequently revoked; the next source check and final
publication revalidate. Revocation cannot atomically retract external work or
already returned bytes. Providers must enforce their own per-request limits.

Bounds: 16 required sources, 32 entities, 1,000 total assets, 32 KiB normalized
input document, 8,192 characters per section and 2 MiB aggregate projected content. All
examples/tests are synthetic. Source revision/evidence remains internal; public
content revisions are fingerprints, not repository commit coordinates.

## Verification and next boundary

```sh
python3 -m unittest discover -s packages/research_assets/tests -v
```

Tests cover stable IDs, type-specific safe projections, all-source failure,
source movement, current authorization, scope isolation, stale revisions,
concurrency, revocation/expiry and audit rollback. No live provider, real data,
legacy database upgrade, browser interaction, production deployment or source
execution was tested or enabled. A UI and actual read-only source adapters are
separate next slices, not implied by this API.

- [Synthetic tests](tests/README.md)
- [Transactional identity seam](../workspace_api/README.md)
- [Workspace policy](../workspaces/README.md)
- [Documentation index](../../docs/README.md)
