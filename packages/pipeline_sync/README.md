# Scoped pipeline provider acquisition

A GET-only client for the existing openLiBing **adapter** protocol, plus a trusted
application service that authorizes acquisition and atomically publishes the
allowlisted result to the [pipeline snapshot API](../pipeline_api/README.md).
This is not a direct openLiBing SDK, live compatibility certification or production
migration. No real token, provider call or production data is used in tests.

## Explicit configuration and current authority

Construct `PipelineSynchronizer` with an initialized SQLite policy store, reviewed
identity providers and one fixed identity provider key. Call `refresh` from trusted
host code with a `Credential` for the user, a `Connection`, an upstream
`BoundCredential` and an expected positive snapshot revision. There is no HTTP
route, scheduler, environment/CLI token discovery or default service address.

A connection includes the exact `PipelineScope(workspace_id, connection_id,
pipeline_id)` and a canonical HTTPS origin. Its upstream credential is bound to
that exact scope **and origin**, with a redacted representation. This is routing
protection, not a vault. A real host must obtain a dedicated least-privilege token
from its approved secret provider and avoid logging request objects/tracebacks.

Trusted initialization must provision two resources for each approved tuple:

- Module `pipeline_connections`, capabilities exactly `("read",)`, with resource
  ID `connection_resource_id(scope)` and text `connection_record(connection)`.
  The schema is only version, origin and exact pipeline ID. There are no secrets.
- Module `pipelines`, with read/write capability, with resource ID
  `pipeline_api.resource_id(scope)` and a valid encoded initial snapshot such as
  `pipeline_api.encode_snapshots(scope, [])`.

Connection records are not editable by ordinary refresh users. Writable connection
modules fail closed. Provisioning and credential rotation are trusted host
operations, not new API endpoints. The existing module permissions mean a current
developer/admin can refresh approved bindings in that workspace; this is not a
per-user connection ACL. A public/shared space still requires explicit membership
and never aggregates private spaces. Provider-visible data is not necessarily
safe to share publicly; a host needs an explicit data publication policy.

## Acquisition and publication

1. Verify the user's opaque credential through the configured identity verifier.
2. In one SQLite snapshot, check current identity binding, active membership,
   `pipelines.write`, the read-only connection module and exact stored binding,
   and the expected target snapshot revision. Capture this flow's exact policy
   realm, local principal ID, issuer, subject and generation, plus this workspace,
   caller membership and both module revisions. Viewer/disabled/archived access
   fails before any upstream request.
3. Immediately before **each** GET, recheck those same current conditions and the
   pinned identity, scoped authorization revisions and binding revision. A changed
   realm or principal pin fails authentication; changed scoped policy revisions
   fail with a value-free `refresh_changed`, even when authority was revoked then
   restored between checks. Network I/O occurs outside the database lock. A
   request that passed this local check may still be sent or finish after a
   revocation commits, including during DNS/TLS setup before any HTTP bytes were
   sent. The next check and final publication revalidate authority; already sent
   bytes cannot be retracted. Local checks and external dispatch are not atomic.
4. List a bounded current subset, then read each exact run. Require explicit
   matching pipeline/run IDs; missing legacy IDs fail rather than guessing.
5. In `BEGIN IMMEDIATE`, recheck identity/assertion validity, membership, modules,
   all flow pins, binding and snapshot revision, then publish the whole validated result with
   one audit event. A race, revocation, expiry or failed/malformed read leaves the
   previous snapshot intact. Equal/older observations for a retained run fail.

The pin is local to one `refresh` call, captured only after its first successful
complete authorization. It is not a bearer credential, persistent grant or cache.
A new call must verify the credential and current policy again; it cannot inherit
an old flow's permission. Unrelated principals/memberships do not cancel a flow:
there is no global policy-epoch equality test. The scoped revisions intentionally
invalidate even harmless edits to those same records rather than guessing their
intent. The final writer reservation fences local concurrent policy changes, CAS,
resource update and audit together, with assertion expiry still checked before
commit. Provider dispatch and database authorization are **not** one transaction.

This closes this consumer's in-flight identity ABA gap, not every grant in the
platform. [Identity administration](../../docs/principal-authority.md) remains
unconditionally closed in service and SQL. Existing workspace memberships still
need the platform-wide transition/regrant safeguards before identity mutations
can be implemented. Whole-policy backup rollback is not solved by flow pins.

This replaces the **current observed subset**, not full execution history.
Upstream list/detail reads are not one transactional snapshot. A missing run is
not a deletion, and a short/empty list proves no full-history completeness. No
retry, terminal-state immutability, background polling or cache fallback is
assumed. Local observation timestamps are taken after each detail response, not
claimed to be upstream event/revision times. The snapshot API's existing
`live_provider_verified = false` remains explicit: synthetic tests cannot certify
a deployed provider. Repeated refreshes require a new expected revision and
strictly newer observations for retained runs.

## Restricted transport

The built-in transport uses only:

- `GET /api/v1/pipelines/{pipeline}/runs?limit={limit}`
- `GET /api/v1/pipelines/{pipeline}/runs/{run}`

The service token is only `X-Service-Token`, never a URL/query value. No latest-run
alias, arbitrary paths, redirects, proxies, cookies, mutation, log or artifact
endpoints are supported. Canonical DNS HTTPS origins only, default TLS validation,
port 443, no credentials/path/query/fragment in the origin. DNS answers must all
be globally routable; private/loopback/link-local/mixed addresses fail. The selected
IP is pinned for the socket while TLS verifies the original hostname, avoiding a
second DNS lookup. This intentionally **does not support the former loopback/HTTP
adapter setup**. Do not weaken checks to connect a private deployment: that needs
an independently reviewed network/transport design and explicit deployment scope.

Defaults: 5 runs (maximum 20), 10-second socket timeout (maximum 15), 256 KiB response
(maximum 512 KiB), at most one list plus one detail per run, no automatic retries.
Body reading is byte-bounded; non-200, compression, wrong content type, duplicate
JSON keys and malformed Unicode fail safely without upstream error bodies.
These are socket inactivity timeouts, **not** a guaranteed total deadline for DNS,
headers or a slow trickle. The hosting worker must impose a whole-job time budget,
concurrency/rate limits and cancellation before real exposure.

Only run IDs/statuses and stage/job names/statuses/times are projected. Logs,
source repository URLs, variables, users, artifacts and arbitrary provider fields
are discarded. `COMPLETED` alone maps to unknown, not success; a known explicit
conclusion is needed. Snapshot codec limits still apply (10000 bytes/200 jobs),
so large responses fail without partial publication. Labels require UI escaping,
and allowlisting is not secret scrubbing. Numeric/naive provider times are not
guessed. Provider compatibility with these stricter contracts remains unverified.

## Verification and remaining work

```sh
python3 -m unittest discover -s packages/pipeline_sync/tests -v
python3 -m unittest discover -s packages/pipeline_api/tests -v
```

Tests use fabricated responses, intercepted sockets and temporary SQLite only.
They exercise credential/connection scope, DNS pinning, TLS hostname, exact GETs,
redaction, projection, revocation/expiry during acquisition, identity and scoped-policy ABA, cross-realm
replacement, multi-run traversal, lock-wait revalidation, atomic CAS/audit rollback
and serving
published data through the existing authorized API. They do not authenticate a
real adapter. A reviewed real identity verifier, approved connection/token,
upstream schema acceptance, visibility policy, whole-job budget and host UI/route
integration are still required. No old service, runner or production data changes.

- [Protocol evidence and exclusions](MIGRATION.md)
- [Tests](tests/README.md)
- [Documentation](../../docs/README.md)

## Review a replacement before publishing it

A trusted integration can choose `reviewed_refresh` instead of `refresh` when a
named operator must inspect the acquired data before replacing the stored subset.
See the [private terminal review contract](REVIEW.md). This is opt-in: it neither
changes the existing trusted method nor adds a button, Cookie route, CLI credential
loader, installed-runtime configuration or projection visible to ordinary readers.
