# Runnable local demo

## Purpose and limits

This is the first executable composition seam, not a migrated production service.
The module-navigation capability filter has been adapted from the predecessor;
the HTTP demo, fixtures and UI shell are new. The root MIT license retains the
original `Copyright (c) 2026 codeasier` notice. No source history, production
configuration, database, credentials, runtime logs or screenshots are imported.

The server exposes a fixed synthetic `demo-developer` identity. It has explicit
membership in `product-alpha` and `commons`, but not `product-beta`. The public
`commons` workspace does not bypass membership. All sample counts and identifiers
are invented. No upstream network calls or writes occur.

**This is not authentication, production authorization or proven multi-tenant
isolation.** Do not load real data, expose this service to a network, put it behind
a reverse proxy, or use it as a production backend. `http.server` is a local
exercise harness, not a deployment recommendation. Production identity,
persistence, admin workflows, sharing and every database/task/storage boundary
remain unimplemented.

## Run and verify

Python 3.12+ runs the server; Node.js 22+ runs the frontend unit tests. No external
runtime dependencies or package downloads are needed.

```sh
PYTHONPATH=backend python3 -m ms_collab --port 8000
# Open http://127.0.0.1:8000, not localhost or a remote hostname.
```

In a separate terminal:

```sh
python3 -m unittest discover -s backend/tests -v
node --test frontend/tests/*.test.mjs
```

The server binds only `127.0.0.1`. It rejects foreign Host/Origin and cross-site
requests, serves a five-file static allowlist, emits no CORS grants, and rejects
write methods. No command-line option disables these restrictions.

## Read-only API

| Endpoint | Contract |
| --- | --- |
| `GET /healthz` | `status` and explicit `synthetic-demo` mode |
| `GET /api/workspaces` | Fixed demo identity and member-visible spaces |
| `GET /api/workspaces/{id}/modules` | Enabled modules intersected with demo grants |
| `GET /api/workspaces/{id}/overview` | Only the authorized space's synthetic overview |
| `GET /api/workspaces/{id}/community-board` | Authorized synthetic board page; see filters below |

Unknown and inaccessible workspaces both return 404. A workspace id never becomes
a filesystem path. Module enablement and capability are separate checks. The UI
never receives the inaccessible space's overview. Clients must discard responses
for an older selection; the shell uses an abort controller and generation check.

## Integration seam

The independently reviewed community-board read model is now explicitly composed
at the server boundary. Membership, module enablement and capability are checked
before parsing filters or reading its projection. The demo does not silently load
installed packages or proxy a live service.
Replacing the demo principal requires a separate authenticated, scoped context
contract and negative tests, not accepting a user or workspace header from callers.

## Browser acceptance

The optional [browser smoke check](../frontend/checks/README.md) starts its own
local server and verifies workspace selection, repeated/interrupted loads, error
recovery and narrow layout. Its tool installation belongs to the isolated public
CI workflow, not to the application runtime. Screenshots contain synthetic data
only and still require human review.

## Verification boundaries

Automated checks cover local HTTP behavior, synthetic membership/module gates,
write rejection, static traversal denial and navigation filtering. They do not
prove login, upstream integration, persistent isolation, production security,
accessibility or visual acceptance. Browser acceptance must separately check
workspace switching, rapid/repeated reloads, narrow layout and error recovery.

## Synthetic community board

The board connects the existing pure read model to a read-only HTTP route and UI.
It is not a provider adapter or persistent/authenticated production service.
Only `kind` (`issue`/`pull`), `state` (`open`/`closed`/`merged`), `q` (title/label,
maximum 200 characters), `limit` (1..100) and `offset` (0..9999999) are accepted.
Duplicate/unknown parameters return 400; denied/unknown workspaces return the same
404 before filter validation. Identity, connection and repository cannot be
selected through a filter. Write methods remain rejected.

Apply filters resets pagination. Editing controls without applying does not
change next/previous requests. Workspace changes clear rows immediately and reset
filters; late old responses are discarded. Sources retain fixed fixture
observation timestamps and the UI labels them as observed subsets, not live or
complete inventories. The overview numbers remain a separate invented fixture,
not totals derived from this board. Shared collaboration contains only its own
synthetic record, never a union of private scopes.

The UI renders titles and labels as text, with no provider links or raw HTML.
Response envelopes and row/source workspace IDs must match the requested scope.
This is defense in depth around synthetic data, **not evidence of production
workspace isolation**. Real authentication, synchronization, administrators,
sharing, persistence and mutation workflows are still absent.
