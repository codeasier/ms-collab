# Community refresh verification (synthetic, CI only)

The existing [workspace connection workflow](../../../.github/workflows/workspace-connections.yml)
runs `python3 -m unittest discover -s packages/community_refresh/tests -v` and
`node frontend/application/tests/community-refresh.cjs`, alongside all of its
previous tests and four screenshots. Its runner, permissions, timeout and prior
assertions are unchanged. Five additional explicitly named synthetic screenshots
cover refresh confirmation, desktop/mobile private review and desktop/mobile
ordinary-reader publication. No real credentials, GitCode calls or deployment.

- `test_session.py`: real host / Cookie / CSRF / SQLite, default false/503,
  config pairing, empty POST and CAS, actual grant replacement invalidation,
  scoped credential mismatch, private candidates, complete approval, unchanged
  current approval retention and changed-content removal; loader/page/final
  authorization, identity ABA, logout/expiry, revocation/config changes, competing
  approval, bounded admission, total deadline and atomic rollback.
- Exact schema and unknown trigger/index rejection; cross-table metadata/audit
  triggers actually reach post-persistence checks and cannot alter the scope or
  this/another active lease. Failure-path tampering keeps reservations occupied.
  Cleanup-uncertain workers never expire out of capacity.
- `test_processes.py`: actual spawned independent Python hosts share the same
  policy/login DBs. Real admissions contend by actor/scope/global capacity; CAS
  and deadline are rechecked after an independently held writer lock. Synthetic
  process barriers are bounded and all children are reaped without passing a
  forced-cleanup failure.
- `test_transport.py`: fixed URL/query/scope, GET-only framed JSON, no redirects,
  private DNS/overlimit/malformed responses and safe diagnostics. Real socketpair
  HTTPResponse coverage exercises `Connection: close` and slow `read1`; blocked
  DNS/cleanup yields bounded waiting and resource-uncertain failure.
- `fixtures.py`: scoped synthetic loader and transport, never imported by product
  code. The HTTPS fixture reuses the real connection-management pages/API and
  accepts a separate explicit refresh-test opt-in. No HTTP provider mock bypasses
  session, authorization, normalization, storage or full approval.

Browser acceptance covers: actual Connections edit clears old content; Cancel /
Escape zero calls; double submit only one acquisition; private 101-row review
with a complete approval set across filters; an ordinary reader sees nothing
before approval, then only approved exact content; changed refresh does not
inherit approval; a lost accepted response is only read back, never retried;
CAS, missing CSRF and true parallel HTTP logout/revocation during an owned GET.
