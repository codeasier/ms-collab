# Browser acceptance

`browser-smoke.cjs` starts its own loopback-only server with an ephemeral port and
checks desktop/narrow layout, allowed workspace navigation, repeated reloads,
stale responses, failed-request recovery and absence of page errors. Board checks
cover filtering, empty results, paging with unapplied draft filters, interrupted
workspace loads, retry, HTML-as-text rendering and rejected cross-scope envelopes. It rejects
non-loopback page traffic. It terminates the server and browser on completion.

The CI-owned workflow installs an exactly pinned Playwright in a temporary
location and sets `NODE_PATH`. Run with the same installed tools:

```sh
node frontend/checks/browser-smoke.cjs
```

The script writes only synthetic UI screenshots (`desktop.png`, `mobile.png`) to
`$RUNNER_TEMP/ms-collab-browser` or the OS temporary directory. Human pixel review
is separate from assertions. Do not publish unrelated logs, screenshots or data.
