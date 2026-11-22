# Research asset browser verification

- `client.test.mjs`: exact scope/query, four-type descriptor consistency, entity
  counts, bounded paging, stable totals, selected content identity, section
  allowlists, published directory and cookie-only fixed GET requests.
- `frontend/application/tests/research-assets-client.test.mjs`: fixed research GET
  ownership, current 401 handling and stale 401/body/error guards.
- `browser.cjs`: actual hosted Chromium over disposable synthetic HTTPS, current
  login and stored catalogs. Select Alpha and Beta without typing IDs, open a
  published empty catalog, and hold a successful Alpha directory response until
  the independently authorized Beta catalog with the same ID opens. Covers four
  types, search/entities, paging, 412, plain-text details, old exact responses,
  pagehide/recheck, real Back, logout, before-open membership/session revocation
  and XSS text. The explicit exact-ID alternative still distinguishes never
  published from a published catalog with no assets.

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
