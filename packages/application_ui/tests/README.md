# Product-entry verification

- `test_application.py`: exact-host static allowlist, public bootstrap boundaries,
  no credential HTML, closed provider, real session/CSRF/CAS and revocation checks.
- `test_artifact_session.py`: real cookie-only catalog, explicit admin opt-in,
  complete candidate/digest/readers/CAS, private fence, current revocation/expiry
  and transaction rollback, no refresh or download fallback.
- `artifact_fixtures.py`, `artifact_browser_server.py`: synthetic published and
  staged snapshots, same real session/policy composition and opt-in HTTPS listener.
  No fixture is imported by production code or represents real OBS verification.
- `browser_server.py`: explicit opt-in temporary HTTPS and synthetic control routes.
  Never imported by ApplicationUI, never a deployment or identity-provider adapter.
- [Browser/client tests](../../../frontend/application/tests/README.md) use the real
  composed service, not mocked authentication or a session-to-bearer bridge.

Return to [composition](../README.md).

The explicit `pipelines` browser fixture mode provisions only synthetic stored
runs for cookie-UI acceptance; it is not provider ingestion or production setup.
The standalone pipeline workflow separately retains provider-to-UI coverage.

- `research_asset_fixtures.py` and `test_research_assets.py`: explicitly synthetic
  four-type projections, empty/unpublished distinction and current cookie policy.
  Browser fixture mode `assets` adds only test catalog/revision-control routes.
- `test_assistant.py` and opt-in `assistant_browser_server.py`: current-owner cookie/CSRF/CAS, fixed-revision history and lifecycle cleanup, without real ACP or execution.
