# Artifact migration slice

Reviewed source baseline: `codeasier/msinsight-obs-web-browser` at
`f6c184e81671b460b9322c9f8bf0cadc70599d76`. Source and destination use the same MIT
license and copyright holder; the root license and source headers are retained.
Only the general behavior described below is re-expressed. No source history,
configuration, real records or runtime output is copied into this public slice.

| Reviewed source file | Reused behavior | Deliberately not copied |
| --- | --- | --- |
| `backend/modules/artifacts/quarterly_store.py` | Explicit quarter selection; parents derived within each quarter; real markers win; missing archives do not create data | SQLite paths, legacy migration marker, sync-task database, fixed deployment timezone |
| `backend/modules/artifacts/object_dao.py` | Directory-level listing; conditional search; file/time predicates; deterministic paging | Global object IDs, provider URI scheme, SQL wildcard behavior, cache writes, provider bucket aggregation |
| `backend/modules/artifacts/artifact_view_dao.py` | Bounded named filter schema, allowed fields/operators/sort, range validation | Global shared-view table, UUID writes, reorder CRUD and implicit workspace assumptions |
| `backend/modules/artifacts/ports.py` | Read capability separation | Permanent provider download URLs and unscoped object-ID access |

## Intentional contract changes

Storage-relative key plus workspace/connection/bucket scope replaces globally
unique provider URI. Timestamps require a timezone rather than assuming a local
wall-clock timezone; the index offset is explicit and defaults to UTC. A directory
prefix must end in `/`; string `%` and `_` are literal, not SQL wildcards. Unsupported
paths and unknown fields fail instead of being normalized or ignored. First
condition must be AND; later OR/AND/AND NOT have SQL AND-before-OR grouping.
Virtual directories do not acquire invented times. Checksum files are not silently
removed. A catalog is an immutable observed snapshot, not a synchronization engine.

This is not a drop-in adapter for the old API or SQLite files. Future imports must
explicitly translate timezone, keys, IDs, view schemas and scope, and validate both
positive and cross-scope negative cases. Do not move production data during this
code migration. Real authentication, durable view changes, query authorization,
provider sync, protected downloads and a user-facing artifact page remain separate
implementation and acceptance work.
