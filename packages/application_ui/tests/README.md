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

- Connection fixture's separate `MS_COLLAB_SYNTHETIC_COMMUNITY_REFRESH_UI=1` mode:
  same real pages/API/SQLite, explicit refresh initialization and scoped synthetic
  GitCode GET transport. Threaded synthetic HTTP permits actual concurrent logout
  while an acquisition is held. Default connection fixture stays refresh-disabled.

Artifact discovery additions: `test_artifact_discovery.py` uses real host/Cookie/
SQLite paths for three workspaces, public-only revision, candidate-free SELECT,
reader/non-reader approver/write-only refresher matrices, current logout/expiry/
identity ABA/provenance, schema/codec corruption, raw-key and aggregate-input
bounds, visible-only pagination and no-provider/read-only assertions. Fixtures
are synthetic and never imported by production factories.

- `test_pipeline_discovery.py`: current Cookie/SQLite pipeline directory, member
  role/lifecycle matrix, three-workspace isolation, valid empty snapshots, exact
  namespace/hash/codec/revision/future checks, embedded-NUL key/JSON suffix and
  invalid-UTF8 rejection, current cursor and final expiry,
  true subject ABA (generation changes twice), actual 256/257 record boundary and
  exact 10,000-byte/1-MiB UTF-8 limits plus one byte. The directory SQL uses the
  existing primary-key prefix range; non-prefix generic resources are not parsed.
- `pipeline_discovery_fixtures.py` and opt-in `pipeline-discovery` browser mode:
  fictional same-name snapshots, temporary pagination controls and a CI-provider
  call trap; never runtime ingestion or provider configuration.

- `test_artifact_fixture_shutdown.py`: the actual opt-in Artifact HTTPS fixture
  in both catalog/approval and discovery modes, with SIGTERM while idle and from
  inside an active WSGI request. The fixture uses the existing `serve_synthetic`
  helper rather than raising `SystemExit` inside wsgiref. Every case requires a
  clean child exit and preserves failure while forcibly reaping any stuck child.
  Existing `unittest discover -s packages/application_ui/tests` CI commands pick
  it up automatically; no new workflow, product shutdown route or upstream call.

### Member status fixture

The optional `module-status` browser fixture mode is synthetic only. It provisions
reader's explicit viewer/developer/admin memberships in three separate spaces via
the existing trusted fixture seam, different local pipeline states, and a bounded
long-key module in commons for the screenshots. It selects `reader-sub` only in
that mode; the original/default provider fixture still selects `admin-sub`.
`module_status_fixture.py` exposes only four fixed origin-checked POST controls in
this mode, with no query/body/target parameters. `test_module_status_fixture.py`
checks the real Cookie/SQLite observations and rejects wrong methods/origins,
other modes, unknown paths and unexpected inputs without applying a control.
These setup controls are not product APIs, enrollment or migration tooling.

The browser test uses a distinct Cookie context, normal/held/two-tab cases before
revocation, a retained beta selection for its post-revocation 404 before Recheck,
and still-accessible commons for the final logout/401 observation. It does not
regrant revoked authority or implicitly reset fixture history to continue.
Current backend status gates remain covered by the existing core/API/host W08
suites; this slice adds a consumer, not a replacement authorization model.
