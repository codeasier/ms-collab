# Authorized responsibility UI

`ResponsibilityUI(api=ResponsibilityApplication(...), origin='https://…')` serves
four allowlisted static files under `/responsibility/` and delegates `/api/`
requests to the explicitly configured responsibility service. It does not create
an identity, discover credentials, start a listener or enable a synthetic mode.
A trusted composition must supply a reviewed verifier and the initialized policy,
community and responsibility stores. No production or old OBS state is migrated.

Origin is fixed trusted configuration, not a request Host-derived destination.
HTTPS is required except numeric IPv4 loopback for isolated acceptance. Host,
scheme, Origin and cross-site fetch metadata are checked. Static assets use a
restrictive same-origin CSP, no-store, nosniff and no-referrer. No query-bearing
static routes, dynamic file paths, embedding or cookie authentication is supplied.
TLS/proxy/header/body/time limits and a reviewed identity integration remain the
deployment's responsibility; this is not a substitute for production acceptance.

The real [responsibility API](../responsibility_api/README.md) gains only a
read-only `/capabilities` endpoint. It returns current explicit read/edit ability
in the same authorized snapshot. Capability display is never authorization to
write later; PUT still rechecks current permissions and the read revision.
Editors receive structured fields/paths/owners/backup editing with a confirmation.
Readers see configuration and the four actual candidate views with pagination.

Scope/response revision matching prevents mixing unrelated snapshots. Conflicts
and uncertain mutations clear local drafts rather than silently overwriting or
retrying; cancellation does not undo an accepted server write. Auth and lifecycle
cleanup is described in the [client contract](../../frontend/responsibility/README.md).

No path evidence upload/acquisition, real Issue assignee mapping or actual review,
assignment, approval, merge, SLO or escalation is added. The screen explicitly
keeps unknown/unverified/observed-only boundaries. Tests and screenshots are
synthetic and do not certify real production credentials or providers.

[Test navigation](tests/README.md) · [Documentation](../../docs/README.md)
