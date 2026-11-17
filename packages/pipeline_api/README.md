# Authorized pipeline snapshot API

Configured, read-only WSGI service for the existing [pipeline analytics
core](../pipelines/README.md). It reads bounded synthetic/imported snapshots from
the current SQLite workspace store; it does **not** connect a CI provider, launch
a server by default, trigger a pipeline, retry a job or read logs. The existing
demo remains unchanged. Python 3.12+, standard library only.

## Trust boundary

`PipelineApplication` requires an existing `SQLiteWorkspaceService`, explicitly
configured identity `Provider` objects, and one fixed `provider_key`. It reuses
the [workspace API's authenticated transaction](../workspace_api/README.md):
protocol verification happens outside the SQLite transaction, then current
identity binding, active membership and the `pipelines` module's `read`
capability are checked in the **same SQLite snapshot** as the selected resource.
Assertion validity is rechecked after lock acquisition and before commit.

Standalone mode uses only a verified bearer credential. The configured unified
host instead supplies the existing explicit cookie/session boundary, without
credential conversion. Arbitrary cookie/header/body actor claims are not authority. Unauthorized callers cannot parse snapshot contents,
calculate counts or access another space's records. Reads linearize at their
SQLite snapshot; later revocation cannot retract an already authorized response.
Upstream token revocation and production login remain separate work.

The module's read permission grants access to stored snapshot resources **in
that workspace**. There is no separate connection ACL in this slice, and no
permission to access live external CI credentials. Every request names the exact
`(workspace_id, connection_id, pipeline_id)` tuple. `commons` is an ordinary shared
workspace with explicit membership, never a union of private spaces. Archived
spaces remain readable under the existing policy. All responses are `no-store`.

## HTTP contract

Base path: `/api/workspaces/{workspace}/pipelines/{connection}/{pipeline}`.
IDs use 1–128 ASCII letters/digits, dots, underscores or dashes, starting with a
letter/digit. There are no inherited management or mutation routes.

| Method | Suffix | Result / query |
| --- | --- | --- |
| GET | `/runs` | Current snapshot list; `offset`, `limit`, optional canonical `status` |
| GET | `/runs/{run}` | Exact safe run details or 404, no query parameters |
| GET | `/durations` | Successful-job duration report; `limit` |

Lists are newest observation first, run ID descending as a deterministic tie-break.
Default list limit is 20; default duration window is 30; allowed limits are 1–100.
The offset is bounded at 100000, and pagination is over one bounded snapshot, not
over remote history. Statuses are `success`, `failure`, `cancelled`, `timed_out`,
`skipped`, `queued`, `running`, `unknown`. Unknown/duplicate query fields fail.

Responses use `{"data": ...}` or a value-free `{"error": "safe_code"}`. Missing
resources return 404; malformed, oversized, cross-scope or future-observed stored
data returns 503 `pipeline_data_unavailable`, **not an empty successful history**.
Responses carry a resource revision and `source.kind = stored_snapshot`, with
`full_history_complete = false` and `live_provider_verified = false`. A positive
job duration does not mean that its containing run succeeded or passed a gate.
Missing/reversed job timings and ambiguous sibling names follow the core's
explicit exclusion/coverage rules rather than being zero-filled.

## Bounded storage contract

`encode_snapshots(scope, runs)` is a pure, allowlisted codec, not an importer or
writer. A trusted future ingestion path must authorize its own operation and
place its result under `Resource(workspace_id, "pipelines", resource_id(scope),
text)`. The resource ID hashes the canonical connection/pipeline tuple; the outer
resource key supplies workspace isolation. A storage hash is **not authorization**.

The version-1 envelope contains only `version`, exact `scope`, and `runs`. Each
run requires the core scope fields, `id`, `observed_at`, canonical `status`, and
`stages`. Stages are `null` when details are unavailable or a list of exact
`{name, jobs}` dictionaries. Jobs contain only `id`, `name`, `status`, `started_at`
and `finished_at`; timing fields may be null. Times require explicit timezones.
Provider status aliases must be normalized explicitly before encoding; raw
`completed` is not accepted as success. Duplicate keys, duplicate run IDs and extra fields fail. Duplicate job IDs are
retained as evidence in details, but excluded from duration samples by the core.

Bounds: 10000 UTF-8 bytes per resource, at most 100 runs, 40 stages per run and
200 total jobs. Names are printable text up to 256 characters. The storage budget
can be reached before count limits. This is a small service seam, not scalable
run-history persistence. Labels are untrusted text: UI consumers must escape
names. These bounds and the field allowlist are **not a secret scrubber**; trusted
ingestion must reject private data and secrets before storing anything public.

## Explicitly not implemented

- Real provider/connection configuration, credential ownership, fetch/poll,
  webhook ingestion, retry invalidation or durable full history.
- Trigger/cancel/retry/log/download/write endpoints, cross-space aggregation,
  connection-level ACLs, background jobs or merge/deploy gates.
- A frontend integration, real login verifier, TLS, rate limits, trusted proxy
  header normalization, request/concurrency limits or production deployment.

Do not wire a test verifier into a real deployment. Before exposure, satisfy the
workspace API's deployment requirements and design reviewed ingestion and
connection authorization separately. This change does not touch the previous
service or migrate production data.

## Verification

```sh
python3 -m unittest discover -s packages/pipeline_api/tests -v
python3 -m unittest discover -s packages/pipelines/tests -v
python3 -m unittest discover -s packages/workspace_api/tests -v
```

[Synthetic regression coverage](tests/README.md) · [Documentation](../../docs/README.md)

## Revision-pinned stage/task observations

`GET /runs/{run}/tasks?revision={revision}&offset=0&limit=20&status=failure`
reads the same authorized resource and SQLite snapshot as the existing API.
`revision` is mandatory, canonical positive decimal up to 999999999; a missing
revision returns 428 and a changed resource returns 412, never a silent retry.
The existing offset/limit/status bounds apply. This paginates at most 200 stored
tasks, not remote history. Unknown fields, duplicate queries and writes fail.

The response retains `scope`/`source`, a selected `run` summary, complete stage
and global status-count `coverage`, the exact `filter`, and `total`/`offset`/
`limit`/`items`. Each task has its original zero-based `stage_index`/`job_index`,
the same allowlisted job fields, and `id_occurrences` across the entire run.
Duplicate provider IDs are retained and explicitly ambiguous, not conflated.
Stage arrays include empty stages; counts are not inferred stage results, and
`stage_status_supplied` is always false. Missing details remain distinct from a
known empty topology. Read revocation/expiry and value-free corruption errors
reuse the original transaction boundary; no provider or writer is added.

- [Why logs remain closed](LOG_BOUNDARY.md): provenance, privacy limits and the
  prerequisites for any separately reviewed log protocol.
- [Task UI](../../frontend/pipelines/README.md): cookie/bearer adapters both use
  the same typed read path and version fence, never credential conversion.

## Stored scope directory

`GET /api/workspaces/{workspace}/pipelines/scopes` lists current readable stored
snapshot tuples and revisions, including valid empty snapshots. It uses module
read authority, not Artifact publication/grant semantics. See [the directory
contract](DISCOVERY.md) for namespace binding, pagination, UTF-8 budgets, the
existing global-loader limitation and the unified cookie picker acceptance path.

Directory factories must now explicitly supply `directory_cursors=ReceiptStore(policy)`
after separately initializing the [factory-only auxiliary schema](../directory_cursors/README.md).
Omitted configuration returns authorized 503, not a tuple fallback, and does not
change manual exact-scope routes. This is not a supported service_runtime migration.
