# Pipeline provider protocol provenance

## Evidence boundary

Reviewed source revision `f6c184e81671b460b9322c9f8bf0cadc70599d76`,
`backend/modules/pipelines/pipeline_service.py`, specifically
`adapter_list_runs`, `adapter_run_detail`, `_validated_run_payload` and `_request`,
plus the corresponding request/response unit tests. The source uses MIT,
copyright (c) 2026 codeasier; see [LICENSE](../../LICENSE).

The source consumer demonstrates these capabilities: list runs with a `limit`,
read one run through `/api/v1/pipelines/{pipeline}/runs/{run}`, JSON responses,
`X-Service-Token` authentication, and pipeline/run identity validation. This is
**consumer-side source evidence only**, not a public provider specification or
an independently executed external acceptance test. The separate adapter's
implementation and deployed schema are not in this migration evidence. No direct
openLiBing upstream endpoints or status-code meanings have been invented.

Newly authored code preserves those narrow request shapes and writes only a
local, current-authority snapshot. It deliberately requires explicit pipeline ID
in list/detail records, explicit-zone times and HTTPS/public-DNS transport. A
legacy adapter that omits these fields, uses only `COMPLETED`, numeric times,
HTTP/loopback, or another response shape needs a separately evidenced adapter
change; it is not silently treated as compatible. The hosting integration must
verify this contract before supplying a real token.

## Excluded from publication

No original source fixture, raw log, history, runtime configuration, operational
address, credential, production source binding, repository/operator inference,
user profile, deployment procedure or private incident record is copied. Tests
are fictional and newly written. Existing trigger/retry/log/artifact warmers,
watchers, DAO, mutable latest aliases and real upstream write operations are not
ported. The old service and its production environment remain unchanged.

## Public allowlist

Only this package, its synthetic tests, adjacent navigation, and a dedicated
unprivileged GitHub-hosted CI workflow. No new third-party runtime dependency,
secret permission, self-hosted runner, deployment or live acceptance job.
