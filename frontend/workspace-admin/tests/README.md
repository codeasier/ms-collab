# Administration client checks

`node --test frontend/workspace-admin/tests/*.test.mjs` runs pure client checks.
`browser.cjs` launches only the isolated test harness and requires the repository's
locked Playwright tooling. It writes two synthetic screenshots under the runner
temporary directory, never inside the repository. [Parent](../README.md).

Creation retries simulate an accepted request with a lost response; identical retry keys must return one workspace. Additional interactions cover operator isolation, create/archive cancellation, lifecycle/module stale revisions, approved disabled installation, archive immutability, mixed module scope rejection and lifecycle cleanup.

An intervening successful creation must not discard another unresolved creation's
retry key. Transport abort is disabled in browser acceptance so generation guards
must independently stop stale rendering and multi-stage follow-up requests.

## Initial-admin selection acceptance plan

This is a required exact-head CI plan, not a claim of local UT/build execution or
passed acceptance. Preserve the existing lifecycle/invitation assertions and
fixture cleanup while adding both standalone bearer and unified cookie coverage:

- A valid typed local ID does not itself enable creation. Review performs only
  GET; Create requires the exact returned ID and canonical `ias1-` value and
  sends one header without changing the body or treating the hash as a credential.
- Target edits, Cancel/Escape, disconnect/pagehide and malformed/wrong-target
  responses invalidate the choice. Delayed responses cannot repopulate a closed
  or newer dialog, even with ineffective transport abort. Repeated clicks cannot
  issue duplicate writes or automatically chain selection GET into creation.
- A lost accepted response followed by explicit retry produces one workspace and
  audit. For the same body, an ordinary failure, close/reopen or later review
  retains the original key/precondition pair; an intervening successful creation
  cannot erase another unresolved pair. A 409 does not rotate or silently retry.
- Only an explicit 412 permits a later user review to update the selection, still
  retaining the original key. No automatic review or resubmission is allowed.
  Page/session departure retains the existing memory-only cleanup boundary; no
  cross-session persistence is introduced.
- Synthetic actor/target change between review and creation fails without a grant.
  Old valid v2 exact replay remains compatible without a new header, while an
  unused key without it fails. Cookie CSRF/current session, no bearer fallback,
  independent operator authority and all direct-API denials remain enforced.

The precondition has no TTL, expiry timer or consumption to test. It is not proof
of externally verified identity or a substitute for the still-closed identity
transition and real-login gates. See the
[API plan](../../../packages/workspace_admin_api/tests/README.md#initial-admin-selection-acceptance-plan).

## Invitations

`node frontend/workspace-admin/tests/invitations.cjs` starts a separate temporary
store and synthetic verifier. It covers cancel/no-write, lost create-response
replay, addressed identity Join, revoked membership/old invitation denial,
stale-revision revoke race, late inbox after disconnect with ineffective abort,
mixed-scope rejection and pagehide/storage cleanup. It produces
`invitations-desktop.png` and `invitations-mobile.png` beside the lifecycle images.

Invitation acceptance uses one context routing dispatcher. Per-scenario rules are
snapshotted for each request; removing/replacing a rule cannot transfer an active
request to a second handler. All active handlers drain before routing teardown;
errors are not ignored. Node regression tests cover single ownership and a rule
replacement while an old request waits. CI executes three fresh invitation runs
as a regression gate, not retries: any failed run fails the job.

This addresses the overlapping page/context route lifecycle failure observed on
main `63196b3` (run `43706014212`); application authorization checks and every
existing invitation assertion remain unchanged. See [Playwright routing lifecycle](https://playwright.dev/docs/api/class-page#page-unroute-all).

## Invitation recipient Review acceptance

These additions are pending exact-head remote CI execution, not local test or
visual-acceptance claims. Both existing Node roots register seven named cases
from `invitation-client-cases.mjs`: exact wire tuple/body/header; canonical paths
and option isolation; full response binding; exact-code 412 recognition; stale
GET/POST outcomes before invalidation; stale decoded bodies; current 401 behavior.
The Cookie registration also asserts no clear/onInvalid for stale 401 and current
session invalidation for a current one. Unreadable/extra-field/other-code 412 does
not authorize selection replacement. Non-invitation behavior remains unchanged.

`invitations.cjs` and `frontend/application/tests/browser.cjs` both invoke
`exerciseInvitationReview` from `invitation-review.cjs`. The invoked real-page
scenarios retain the original assertions and add:

- No-review submit is a no-op; Review alone never writes. Input/role change-back,
  Cancel/Escape, in-page reload/scope changes, pagehide and supported Back clear
  current selection. Wrong tuples/extra fields cannot enable Send.
- A real 200-character synthetic principal is reviewed at desktop and 390px.
  Wrapped status, visible actions and no overflow are asserted; Cookie checks
  computed contrast. Exact added images are `invitation-review-desktop.png` and
  `invitation-review-mobile.png` in each existing UI artifact, with every old
  screenshot retained.
- A shaped stale value reaches actual SQLite/API 412; state reads verify no
  invitation/grant and no automatic retry. Explicit Review/Send retains key/body.
  Its committed response is then lost; Cancel, edit-back, in-page reload/scope
  roundtrip and explicit retry retain the original key/body/proof without Review.
  The recovered row retains the same ID/expiry, remains unique and grants nothing.
- 400/409/428/unknown or extra-field 412/malformed or wrong-target 200 keep the
  original pair. Old GET success/error/401 released while a newer GET is held,
  and old POST success/error/401/exact412 released while a newer POST is held,
  cannot disconnect, replace selection, mark the newer pair rejected or unlock
  newer busy controls. Abort is deliberately ineffective. A current 401 clears.
- Page departure clears all private UI and browser retry memory; no localStorage
  or sessionStorage is introduced. Mocked adverse replies test races, not server
  business acceptance. The actual supported paths are standalone Bearer HTTP and
  unified Cookie HTTPS; real-provider/production acceptance remains outside scope.

The original standalone invitation create/Join/revoke and late-real-write journey
still runs independently three times under the unchanged workflow gate. A helper
file existing on disk is not counted as evidence without these executed callers.
