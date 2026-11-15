# GitCode login: evidence and activation gate

Status on **2026-11-15**: **blocked for real-provider activation**. This is a
protocol-evidence review and an acceptance plan, not a live login result. No
provider request, real app credential, account, production callback or existing
service configuration was used or changed. The product entry remains usable as
a configured shell, but an absent bound-code adapter keeps login unavailable.

## Evidence ledger

| Inspected primary material | Established by that material | Not established |
| --- | --- | --- |
| [Official OAuth guide](https://docs.gitcode.com/en/docs/apis/oauth/) and [Chinese guide](https://docs.gitcode.com/docs/apis/oauth/) | Authorization request, returned state/code, token exchange and current-user lookup | S256 parameters, verifier enforcement, downgrade rejection or OIDC nonce validation |
| [Official token API](https://docs.gitcode.com/docs/apis/post-oauth-token-grant-type-authorization-code-code-code-client-id-client-id-client-secret-client-secret/) | Documented query fields and multipart client secret; token lifetime and scopes | PKCE request encoding, redirect binding at redemption, atomic code replay handling and concrete error semantics |
| [Official current-user API](https://docs.gitcode.com/docs/apis/get-api-v-5-user/) | Documents an `id` field of type string | Identifier stability/non-reassignment semantics; that an arbitrary bearer token was issued for this application, a signed audience claim or a workspace role |
| [GitCode's documentation repository](https://gitcode.com/GitCode/GitCode-Docs) | The old documentation repository is marked deprecated and points to the current documentation site | Authorization-server implementation or deployment provenance |
| [A third-party Go client's `oauth.go`, pinned at `aa5c85c`](https://github.com/yi-nology/go-gitcode/blob/aa5c85ca3f2a5e05643c0a79d0b77cf970adf584/oauth.go) | This source constructs authorization URLs and sends code-exchange requests | Server-side checks: a client implementation is not evidence of the deployed authorization server |

The targeted public documentation/source search did **not locate a verifiable
server implementation tied to the deployed GitCode authorization service**.
This is a bounded search result, not proof that no source or PKCE support exists.
SDK examples, generic OAuth libraries, GitLab/GitHub compatibility, platform
branding and blogs about unrelated mirrored projects cannot close this gap.
Current documentation branding also does not authorize changing fixed endpoints.
Do not try alternate hosts, discovery URLs or undocumented token fields with
real credentials to guess which combination works.

[RFC 9700 §§4.5.3 and 4.8](https://www.rfc-editor.org/rfc/rfc9700.html#section-4.5.3)
requires protection against code injection and describes downgrade checks.
[The S256 protocol](https://www.rfc-editor.org/rfc/rfc7636.html#section-4) binds a
code to a per-transaction verifier. Local state/browser binding addresses a
different boundary; it cannot prove that an upstream verifier is enforced.
OIDC would require its own authoritative issuer/key/audience/nonce contract,
not merely adding a `nonce` parameter to an OAuth request.

## Delivered locally, without activating GitCode

- [Wire client](../packages/gitcode_identity/README.md): fixed documented endpoints,
  bounded TLS transport, no redirects/proxies/retries, minimized identity evidence.
  It has neither `verify` nor `exchange_bound_code` and cannot fill the login slot.
- [Login core](../packages/login_state/README.md): one-use state/browser binding,
  fresh S256 material, expiring sessions, rotation and explicit revocation.
- [Session/policy boundary](../packages/session_api/README.md): current authority
  in one attached SQLite transaction, strict HTTPS/Origin/cookie/CSRF handling.
- [Unified host](../packages/application_host/README.md) and
  [product entry](../packages/application_ui/README.md): configured cookie-only
  composition. Public bootstrap metadata is not an authenticated identity.
- [Cross-layer gate regressions](../packages/gitcode_identity/tests/test_login_gate.py):
  unavailable start/callback create no sessions, mint no cookies and attempt no
  connections; a previously allocated attempt, PAT, request capability flag,
  synthetic environment credential or wire-client object cannot enable login.

Run the no-credential checks from the repository root:

```sh
python3 -m unittest discover -s packages/gitcode_identity/tests -v
python3 -m unittest discover -s packages/login_state/tests -v
python3 -m unittest discover -s packages/session_api/tests -v
python3 -m unittest discover -s packages/application_host/tests -v
python3 -m unittest discover -s packages/application_ui/tests -v
```

These are **offline regression checks**, not a provider-conformance certificate.
An injected `BoundCodeExchange` is trusted server code. A wrapper that simply
converts user lookup evidence to `VerifiedIdentity`, or sets a capability boolean,
would defeat the intended contract; the interface cannot audit its own adapter.
No such wrapper, activation flag, default provider or network probe ships here.

## Stage 1: resolve the precise protocol contract

Before implementing a real adapter, obtain provider-owned documentation or an
authenticated maintainer answer for the target app type and endpoint version:

1. Does the authorization endpoint record an S256 challenge against each code?
   Specify exact authorization fields and the redemption verifier's location,
   encoding and client authentication format. Is PKCE enabled for this app type?
2. Does redemption reject a missing/wrong verifier for a challenged code, and
   reject a supplied verifier when the code was issued **without** a challenge?
   Can S256 be silently treated as plain or an unknown parameter ignored?
3. How are codes bound to the client and exact registered callback? What exact
   redirect value, if any, must be sent during redemption? What is the code TTL,
   and is single use atomic under concurrent redemption?
4. Which minimal scopes expose the user ID, and what guarantees its stability and
   non-reassignment after rename/deletion/recreation? How are expired/revoked
   tokens rejected, and what trusted evidence bounds the local session lifetime?
5. Are equivalent security properties available through an authoritative OIDC or
   other transaction-bound flow? If proposed, provide the full validation contract;
   equivalence requires a separate design/review, not an automatic fallback.

Keep the answer's date, provider/version and a shareable evidence reference. A
server source citation needs a fixed revision, relevant validators and evidence
that this revision/configuration applies to the tested service. Do not publish
private support correspondence, account identifiers or configuration merely to
make the public ledger look complete. This plan does not send a support request.

## Stage 2: isolated, explicitly authorized provider acceptance

**Not run.** Prerequisites: approval to exercise the named service, separate
non-production OAuth application(s), controlled test accounts, minimal scopes,
exact registered HTTPS callbacks, a trusted test host, a private credential store
and a reviewed adapter implementing Stage 1's confirmed wire contract. Do not
reuse an existing PAT, production app, production database or production callback.
Where a cross-client case needs a second app, authorize that app explicitly too.
An unavailable prerequisite is `not_run`, never `pass`.

Use only codes created by the controlled test accounts. Keep code, verifier,
secret and access/refresh tokens in the isolated process/private store. Disable
request URL/body/header, exception-local, browser HAR and reverse-proxy tracing
before running: the documented exchange includes a code in the query. No token
or code is a command-line argument, issue attachment, screenshot or CI artifact.

For each negative case use a **fresh, valid, unredeemed code** and alter only the
field under test. Bracket the case with separate successful controls using the
same app/account/callback configuration. A timeout, 5xx, invalid client secret,
unregistered callback or already-used/expired code is **inconclusive**, not proof
of verifier enforcement. Observe the provider denial separately from the local
adapter's refusal; local rejection alone cannot establish server behavior.

| Case | Isolated procedure | Required observation |
| --- | --- | --- |
| S256 control | Fresh random verifier A; authorize with its S256 challenge; redeem using A | Success for the controlled identity; exact documented semantics |
| Wrong verifier | Code challenged with A; redeem with independent valid-format B | Provider rejects; no local assertion/session |
| Missing verifier | Code challenged with A; omit verifier at redemption | Provider rejects; no local assertion/session |
| Cross-attempt injection | Two browser attempts have distinct state/browser/verifier pairs; substitute code A into B while preserving B's valid state/browser | Provider rejects B's verifier for code A; no local session |
| Unbound-code downgrade | Authorize without a challenge; if a code is issued, redeem it with a fresh verifier | Authorization denied, or redemption denied; never accepted as bound-code login |
| Method integrity | Request S256, then attempt the plain challenge string as verifier; separately test a provider-documented invalid method | S256 is not silently downgraded; unsupported requests are rejected, not ignored |
| Client/redirect binding | Use independently valid, authorized client/callback configurations to exercise the mismatches confirmed in Stage 1 | Provider rejects cross-client/callback misuse; client-auth/config failures do not count as proof |
| Replay/concurrency | Redeem one challenged code correctly, then repeat; also race two identical valid redemptions | No second successful grant; local callback still one-use independently |
| Expiry | Use a separate code only after its documented TTL, with an unexpired control | Expired code denied; local attempt/session deadlines also hold |
| Provider error/outage | Decline consent; then exercise an approved unavailable-provider fixture | No new session and no replay/fallback; unavailable is not anonymous success |

Do not demand that the provider globally disable every legacy plain/no-PKCE flow;
verify the precise S256-bound flow this adapter uses and the downgrade denial
above. If it cannot safely distinguish a bound code, stop. Do not fall back to
state-only login or to arbitrary bearer-to-user lookup.

## Stage 3: reviewed adapter and browser/policy acceptance

Only after Stage 1 and isolated conformance are satisfactory should a separate
implementation PR introduce the real adapter. It must validate fixed issuer,
client/callback and assertion lifetime before returning `VerifiedIdentity`, keep
provider credentials out of the browser, and preserve all existing transport and
failure boundaries. Confirm exception paths do not expose credential material.
Independent review and exact-head CI remain necessary; a filled checklist is not
permission to merge, deploy or change credentials/security settings.

On the separately approved non-production host verify an actual cross-site
callback with trusted TLS, correct `__Host-` cookies and no token in JS/URLs;
login cancellation, a second tab, repeated callback and stale-cookie recovery;
CSRF/Origin denial; logout, expiry and current account/membership revocation;
workspace/public-space isolation and the configured module routes. Local subject
revocation removes existing sessions, not an in-flight/new upstream login. Account
disable must therefore continue to deny policy operations independently.

Use a sanitized report containing only source commit, test date, provider/app-type
version, controlled case IDs, `pass`/`fail`/`inconclusive`/`not_run`, and the reviewer
assessment. Refer to private evidence without uploading it. A failed/inconclusive
required case leaves the provider disabled. Re-test when the provider contract,
app type, adapter, callback, host trust or security-relevant configuration changes.
No live test or production rollout is scheduled or performed by this document.

[Documentation index](README.md)
