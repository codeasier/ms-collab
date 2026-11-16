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

- `pipeline_task_fixtures.py` and `test_pipeline_tasks.py`: fictional provider
  projection and cookie-policy task pages. Opt-in browser mode `pipeline-tasks`
  adds only temporary synthetic revision control, never a product mutation.
- `test_gui_smoke.py`, `gui_browser_server.py`: supported-module/navigation
  parity, GUI static boundary, current cookie authority and opt-in synthetic HTTPS
  fixture. The fixture is not imported by production code.

- `test_connections.py`, `connections_browser_server.py`: opt-in community policy
  management, real cookie/CSRF denial, exact static routing and synthetic HTTPS.
  The provider, identities, policy and observations remain test fixtures only.

- `synthetic_shutdown.py`, `test_synthetic_shutdown.py`: test-only graceful WSGI
  shutdown, including SIGTERM during an active request and while idle. The
  serving thread never raises SystemExit inside a request; shutdown runs on a
  separate thread and the child must exit cleanly. Unified, community, connection and assistant
  fixtures use this helper; no product shutdown or deployment behavior changes.

- [Bound connection fixture startup](test_connections_fixture.py): real synthetic HTTPS child startup, strict persisted approval seeds and clean shutdown.
- `test_assistant_fixture_shutdown.py`: real opt-in assistant HTTPS fixture,
  deterministic SIGTERM from inside its WSGI request and idle shutdown. Only a
  disposable test process is signalled; failures always reap it and remain failures.

- `test_application_fixture_shutdown.py`: the actual unified HTTPS fixture in
  both default and closed-provider modes, with SIGTERM while idle and from inside
  an active WSGI request. Failures reap their child without converting forced
  cleanup into passing acceptance; no product shutdown behavior changes.
