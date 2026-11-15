# Browser acceptance tooling

Playwright is a development-only dependency, pinned with its transitive package
and npm integrity values in `package-lock.json`. The application itself has no npm
runtime dependencies. CI copies this directory's two manifests to a temporary
location and uses `npm ci --ignore-scripts` there; do not commit installed packages
or browser binaries.

The [public CI workflow](../../.github/workflows/public-ci.yml) installs Chromium
for this locked Playwright version, then runs
`node frontend/checks/browser-smoke.cjs` with `NODE_PATH` pointing to the temporary
installation. Only two synthetic screenshots are uploaded; see the
[CI boundary](../../docs/public-ci.md#browser-acceptance).
