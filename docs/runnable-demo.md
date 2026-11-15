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
requests, serves a four-file static allowlist, emits no CORS grants, and rejects
write methods. No command-line option disables these restrictions.

## Read-only API

| Endpoint | Contract |
| --- | --- |
| `GET /healthz` | `status` and explicit `synthetic-demo` mode |
| `GET /api/workspaces` | Fixed demo identity and member-visible spaces |
| `GET /api/workspaces/{id}/modules` | Enabled modules intersected with demo grants |
| `GET /api/workspaces/{id}/overview` | Only the authorized space's synthetic overview |

Unknown and inaccessible workspaces both return 404. A workspace id never becomes
a filesystem path. Module enablement and capability are separate checks. The UI
never receives the inaccessible space's overview. Clients must discard responses
for an older selection; the shell uses an abort controller and generation check.

## Integration seam

Modules should be pure application capabilities composed at the server boundary,
not import the server or read credentials at import time. A future community-board
adapter can register its descriptor and scoped reader only after its independent
review. This demo does not silently load installed packages or proxy a live service.
Replacing the demo principal requires a separate authenticated, scoped context
contract and negative tests, not accepting a user or workspace header from callers.

## Verification boundaries

Automated checks cover local HTTP behavior, synthetic membership/module gates,
write rejection, static traversal denial and navigation filtering. They do not
prove login, upstream integration, persistent isolation, production security,
accessibility or visual acceptance. Browser acceptance must separately check
workspace switching, rapid/repeated reloads, narrow layout and error recovery.
