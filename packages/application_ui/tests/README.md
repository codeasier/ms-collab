# Product-entry verification

- `test_application.py`: exact-host static allowlist, public bootstrap boundaries,
  no credential HTML, closed provider, real session/CSRF/CAS and revocation checks.
- `browser_server.py`: explicit opt-in temporary HTTPS and synthetic control routes.
  Never imported by ApplicationUI, never a deployment or identity-provider adapter.
- [Browser/client tests](../../../frontend/application/tests/README.md) use the real
  composed service, not mocked authentication or a session-to-bearer bridge.

Return to [composition](../README.md).

The explicit `pipelines` browser fixture mode provisions only synthetic stored
runs for cookie-UI acceptance; it is not provider ingestion or production setup.
The standalone pipeline workflow separately retains provider-to-UI coverage.
