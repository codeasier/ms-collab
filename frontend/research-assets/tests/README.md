# Research asset browser verification

- `client.test.mjs`: exact scope/query, four-type descriptor consistency, entity
  counts, bounded paging, stable totals, selected content identity, section
  allowlists and cookie-only fixed GET requests.
- `browser.cjs`: actual hosted Chromium over disposable synthetic HTTPS, current
  login and stored catalog. Covers four types, search/entities, paging, empty vs
  unpublished, 412, scope input clearing, old response cancellation, details,
  pagehide/recheck, real Back, logout, membership/session revocation and XSS text.

```sh
node --test frontend/research-assets/tests/*.test.mjs
node frontend/research-assets/tests/browser.cjs
```

[Workflow](../../../.github/workflows/research-assets-ui.yml) uses locked browser
tooling and uploads exactly desktop, mobile and detail synthetic PNGs. The TLS
certificate exception applies only to the disposable browser context, not the OS
trust store. Fixtures live under `packages/application_ui/tests` and are never
imported by production factories. No real provider/credentials/data, external
writes, source parsing, execution or deployment acceptance is implied.
