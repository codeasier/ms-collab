# Explicit Community refresh in the unified host

This opt-in closes the Connections → explicit Refresh → private review → complete
exact-content approval → ordinary reader board loop using the existing Cookie /
CSRF and same-SQLite authority. It is not a real GitCode integration acceptance,
production server, provider registration, periodic job, or remote-write feature.

Trusted setup must separately install the Community policies and call
`initialize_refresh(sessions.policy)`. Configure **both**
`HostConfig(..., community_refresh=True)` and
`ApplicationHost(..., community_refresh=CommunityRefreshConfiguration(loader))`.
The flag requires the hosted Community module; mismatched/missing configuration
is rejected. The host builds its own `CommunityApplication` and controller using
its own `SessionWorkspaceApplication`. It accepts no externally authenticated
service, provider object, request-selected URL, token, credential reference or
actor. Default configuration still advertises `refresh: false`; an otherwise
valid POST receives 503 without credential loading, admission or network.

The trusted loader is called as `loader(scope, stored_credential_ref)` and must
return a `BoundCredential` for that exact scope. It must be bounded, synchronous
and leave no owned asynchronous resources. It is neither a credential discovery
mechanism nor permission to read an environment file. No real credentials are
provided or loaded by the package, host or tests.

`POST .../community/<connection>/<owner>/<repo>/refresh` retains the exact `{}`
body, `If-Match`, current Cookie/CSRF and explicit admin+refresher grant contract.
The existing UI asks for confirmation, closes the dialog before awaiting the
write, suppresses repeat submits, and only reads back an uncertain outcome.
Cancel/Escape makes no provider call. Scope changes, logout and page lifecycle
clear stale responses, not the server's already-accepted work.

## Limits and lifecycle

- Same-DB writer-locked admission: at most **2 active globally, 1 per actor and
  1 per exact scope**, including independently configured processes.
- **30 seconds** across admission wait, loader, all pages, normalization and final
  publication guard. Each page keeps the Connection's fixed byte/page limits.
- Real transport: only `https://api.gitcode.com:443`, fixed issues/pulls GET paths,
  validated stored scope/query, public-only pinned DNS/TLS; no environment proxy,
  redirect, retry, compression, alternate URL or remote mutation.
- Snapshot, retained current exact-content approvals, metadata-only audit and
  lease completion are committed atomically. Rejected/late observations do not
  replace any content or approval.
- Unknown cleanup or still-running DNS/I/O keeps its lease **active indefinitely**.
  Expiry is a publication fence, not evidence that capacity can be reclaimed.

See [design](DESIGN.md) for bounded-wait versus cancellation limitations and
[tests](tests/README.md) for CI-only synthetic coverage. Real login/identity
transition gates, runtime configuration, startup and deployment remain unchanged.
