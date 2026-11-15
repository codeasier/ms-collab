# Synthetic refresh verification

Run `python3 -m unittest discover -s packages/artifact_refresh/tests -v` from the
repository root. Fixtures are synthetic and never imported by production hosts.
The session integration is tested separately from live provider acceptance.

Return to [package navigation](../README.md).

- [Session transactions](test_session.py): private-to-approved lifecycle, current
  rights/CSRF, CAS and concurrent limits, revocation/expiry and rollback.
- [Fixed transport](test_transport.py): DNS denial, framing bounds, real
  socketpair interruption and cleanup ownership; no external provider calls.
- [Temporary HTTPS fixture](browser_server.py) and [browser acceptance](../../../frontend/application/tests/artifact-refresh.cjs):
  same cookie across refresh/review/approval/catalog, canceled and stale actions,
  genuine concurrent logout/revoke, responsive synthetic screenshots. The fixture
  requires explicit environment opt-in and temporary certificates.

- [Generation authority regressions](test_authority.py)
- [Real HTTPS fixture startup](test_browser_fixture.py): bound-publication state
  is valid JSON before any browser/provider call; child cleanup is checked.
