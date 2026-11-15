# GUI smoke migration boundary

## Verified source

Inspected the OBS service at `f6c184e81671b460b9322c9f8bf0cadc70599d76`:

- `docs/gui-smoke.md` describes the real BFF/panel split, visibility, terminal
  cache, task/list queries, report isolation and incomplete capabilities.
- `backend/modules/gui_smoke/gui_smoke_service.py` implements live task/queue/
  catalog reads and submit/cancel/retry/reproduce; recursive field filtering,
  receiver projection and short-request observations are not workspace isolation.
- `backend/modules/gui_smoke/gui_smoke_controller.py` supplies read filters,
  authenticated write boundaries and separate log/download/report routes.
- `frontend/src/modules/gui-smoke/model/gui-smoke-table.js` and
  `task-list-controller.js` distinguish supported task states and cursor/page
  observations rather than inventing complete history from a partial page.

Also inspected the documentation's pinned external panel contract in
`codeasier/insight-ci`, commit `d8bedfe7f71a64cee2098f5e8d5701863ada550c`,
`gui-smoke-panel/api_v1.py` and `tasks.py`. The panel owns its queue and execution.
Its owner/observer modes and historical internal-network exposure are not an
application-level identity, workspace or public execution sandbox boundary.
The pinned panel does not establish that a current deployment supports newer
exact-reproduction behavior; source documentation explicitly separates those
capabilities from already deployed versions. No deployment was contacted.

This package is a clean implementation under the existing codeasier MIT license.
No production address, path, configuration, notification email, credential, old
history or runner code was copied into the public tree.

## Selection

| Existing capability | Safe increment here |
| --- | --- |
| Task briefs/details and reported stages/results | Strict already-selected display model; null and unknown remain distinct from success/zero. |
| BFF terminal read cache | Explicit bounded published snapshot, not a live cache/fetch fallback. |
| Frontend task filters and page state | Exact server filters and revision-fixed local pagination. |
| Platform read permission | Current transactional workspace/module policy plus explicit connection audience captured at publication. |
| Current observation/provenance | Timestamp and stored-source metadata; never claims live verification or execution proof. |
| Global panel queue/health/catalog | Not proxied; cross-repository queue data could leak, and catalog GET may invoke external git/network work. |
| Submit/cancel/retry/reproduce/schedule | Not migrated. No unisolated execution or production runner is started. |
| Logs, download bundles and inline reports | Not migrated; potentially sensitive text/files and active report content require separate scope and isolation designs. |
| Notification identity and recipients | Not migrated or exposed. No mailboxes, submitter identity or notification side effects. |

A future external adapter must authenticate the panel, establish exact repository
and audience authority, bound and validate source data, normalize explicit
timezones, and handle credentials/network restrictions without copying legacy
internal-network trust. Execution additionally needs a reviewed sandbox/runner
isolation design and explicit lifecycle/unknown-dispatch handling. A successful
read of a task snapshot grants none of those capabilities.
