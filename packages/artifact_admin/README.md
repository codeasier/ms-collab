# Artifact administration API and browser

A user-facing management surface over the [artifact synchronization service](../artifact_sync/README.md).
It refreshes a **private candidate**, displays its **entire metadata collection and
configured reader audience**, and publishes only after explicit exact-content
approval. It does not add file downloads, real identity/secret provisioning or
production data migration. Cloud compatibility remains separately unverified.

## Trusted composition

Create an `ArtifactSynchronizer` with the initialized policy/index database, real
reviewed identity verifier and approved credential loader described in its package.
Then construct `ArtifactAdministrationAPI(service=service)` and
`ArtifactAdministrationUI(api=api, origin="https://your-reviewed-host")` in trusted
host code. The management page is `/artifact-admin/`. There is no default provider,
actor, credential, bucket, listener or anonymous demo login. This wrapper does not
automatically change the existing local demo or reader page.

The factory reuses the exact same verifier, current subject binding, policy store
and provider dependencies. The internal verified-assertion methods are trusted
Python seams, never HTTP-selected identities or callbacks. The existing trusted
Python `refresh`, `review` and `approve` methods remain compatible.

The host must enforce TLS, normalized headers, bounded request reads, request/job
deadlines, secret handling and deployment policy. Refresh is synchronous and
bounded by the existing provider scan limits; cancelling a browser request cannot
undo a change already accepted by the server. There is no scheduler or automatic
retry. Keep the supplied origin guard (or an equivalent reviewed integration),
and never use the synthetic acceptance verifier with real data.

## Fixed routes

Base: `/api/workspaces/{workspace}/artifact-admin/{connection}/{bucket}`.
All routes require an opaque bearer credential accepted by the configured verifier;
cookies and self-reported actor/role headers do not authenticate. IDs select only
stored exact-scope policy, never a remote URL or credential.

| Route | Contract |
| --- | --- |
| `GET /status` | Current explicit administrator capabilities, index revision, approved revision and whether a private candidate exists; no private rows, readers, remote configuration or credentials |
| `GET /review` | Requires `If-Match: "revision"`; returns one complete bounded candidate, exact reader IDs, schema, row count and SHA-256 digest; no filters or pages |
| `POST /refresh` | Requires current `If-Match` and JSON `{}`; reads the configured provider and saves a private candidate only |
| `POST /approve` | Requires current `If-Match`, candidate digest, exact sorted reader IDs and explicit full-candidate confirmation; one atomic approval |

Approval JSON fields are exactly `candidate_digest`, `readers` and
`confirmation: "approve_complete_candidate_to_reviewed_readers"`. The candidate
hash covers the complete canonical snapshot, including scope and observation.
The reader list is independently compared with current configuration in the final
transaction. A changed reader audience, candidate or revision fails with `412`;
there is no silent approval of an unseen audience or new candidate.

Status never grants authority. Every operation rechecks current identity,
membership, active admin role, module capability and its explicit scope grant.
Refreshers need write permission; reviewers need read permission; HTTP approvers
need **both** read and write, plus the approver grant. Status hides actions absent
from this combination. A normal reader cannot use status/review to enumerate
private candidates. A shared workspace still requires membership and an explicit
grant; operator status is not a bypass.

## Complete review, conflict and privacy behavior

The browser does not approve a selected page or filtered subset. It validates the
whole response: scope, revision, every row, unique literal keys, timestamps, sizes,
all reader IDs, complete count and the snapshot's canonical SHA-256. Dropping a
row, even while changing its reported count, invalidates the digest. All bounded
rows are rendered with text-only DOM nodes in a scrollable collection. There is no
HTML execution, object URL or download link.

To approve, the administrator first loads the complete preview, acknowledges the
candidate and exact audience, then confirms the complete-set count, audience,
scope, revision and digest. Loading a preview or refreshing never approves.
Configured readers include IDs that may not currently have membership; the page
states that distinction instead of implying only today's active members receive
the grant. Approval does not require the refresher and approver to be different
people, though separate grants support that arrangement.

Credentials remain in module memory and are sent only in authorization headers.
Scope edits clear the preview and confirmation immediately. Cancellation plus a
generation guard prevents old responses from restoring a previous scope; `401`,
disconnect and `pagehide` clear the session and private rows. Revision conflicts
clear approval state and require a fresh complete review. An uncertain network
result disables further mutations until current state is reloaded; it is never
automatically retried. No local/session storage, cookies or credential URLs are
used. Browser memory is not a secure erasure mechanism.

The optional `expected_readers` parameter on trusted Python `approve` now offers
the same exact-audience check. The user-facing HTTP route always requires it.
Trusted hosts changing index policy must still follow the original explicit
migration/invalidation rules; this UI does not edit credentials or connection
policies. Publication remains whole-candidate approval, not per-object ACLs,
version-history completeness or deletion reconciliation.

## Evidence and remaining work

[Python tests](tests/README.md) exercise real temporary SQLite policy transactions,
current permissions, complete preview, audience changes, version conflicts,
provider failure, expiry rollback and origin checks. [Browser tests](../../frontend/artifact-admin/tests/README.md)
exercise actual browser-to-HTTP refresh, independent review, publication and reader
isolation using only synthetic cloud XML and identities. Dedicated hosted CI
retains three synthetic screenshots for three days.

Real cloud compatibility, identity enrollment and secret management, production
operations, large-scale history/deletion reconciliation and protected downloads
remain outside this slice. [Documentation index](../../docs/README.md).

The unified cookie host can explicitly enable [configured refresh](../artifact_refresh/README.md).
It reuses these routes but adds same-session persistent admission and bounded
provider ownership. Neither a browser nor a workspace name can configure secrets
or endpoints. An omitted host flag keeps refresh unavailable.
