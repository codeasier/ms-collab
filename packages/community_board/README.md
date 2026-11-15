# Community board: read model

An independently testable first migration slice of the collaboration board.
Python 3.11+ standard library only. It does not start a server, load credentials,
read a database, call a provider, execute commands or write upstream state.

## Try the synthetic data

From the repository root:

```sh
python3 -m packages.community_board product-alpha
python3 -m unittest discover -s packages/community_board/tests -v
```

The example data is fictional. `product-alpha`, `product-beta` and `commons` are
independent scopes. `commons` contains only its own explicitly provided records;
it is **not** an aggregate of private workspace data.

## Integration contract

```python
from packages.community_board.demo import demo_board

# A real caller must first authenticate the principal, resolve membership,
# and authorize this workspace's enabled board capability.
page = demo_board().list_items("product-alpha", kind="issue", state="open")
```

- `Scope(workspace_id, connection_id, repository)` gives records a composite
  namespace, so equal issue/PR numbers in different repositories do not collide.
- `BoardItem` is a small allowlisted record; no raw provider response, user
  profile, description, token or upstream URL is accepted. Titles/labels remain
  untrusted text and must be escaped by a consuming UI.
- `Snapshot` is an immutable observed subset. `observed_at` and item `updated_at`
  require explicit timezones; the model does not invent timezone or freshness.
- `BoardProjection.list_items(workspace_id, ...)` returns stable pagination,
  filtered counts and source observation timestamps. Unknown workspaces fail
  closed. It has no all-workspaces fallback and no implicit sharing.
- `compare_snapshots(previous, current)` refuses different scopes or backwards
  observations. Missing records are **unavailable**, never inferred closed or
  deleted. A first snapshot is a baseline. Differences are observations, not
  proof of authoritative event ordering or full repository coverage.

This library is **not** an authentication/authorization boundary. A caller that
passes another workspace's ID can read its loaded projection. The application
must enforce membership and capability access before every query. Persistent
storage, authenticated HTTP integration, provider synchronization and the visual
board have not been migrated by this package.

## Navigation

- [Migration scope and provenance](MIGRATION.md)
- [Tests](tests/README.md)
- [Project migration plan](../../docs/migration-plan.md)
