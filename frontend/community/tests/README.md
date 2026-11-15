# Community UI verification

CI runs these commands with Python 3.12 and Node 22:

```sh
python3 -m unittest discover -s packages/community_ui/tests -v
node --test frontend/community/tests/*.test.mjs
NODE_PATH=/path/to/locked/browser-tools/node_modules node frontend/community/tests/browser.cjs
```

Use the exact Playwright lock from `scripts/browser`. The browser harness starts
only a numeric loopback synthetic service and blocks any external browser
request. `MS_COLLAB_BROWSER_EXECUTABLE` can select an already installed browser
for a local check; CI uses Playwright's matched Chromium. No production provider
or credential is used. Output screenshots are written outside the checkout to
`$RUNNER_TEMP/ms-collab-community` (or the system temporary directory).

Coverage includes current capability gating; reader, non-reader reviewer and
refresh-only roles; safe title/label rendering; complete review pagination;
filters preserving off-page approvals; cancel/Escape; duplicate confirmation;
version conflict without blind retry; refresh invalidation; cross-scope late
responses; malformed payload fail-closed; empty observations; mobile overflow;
Back/Forward lifecycle cleanup, disconnect during an accepted mutation and authentication expiry.
