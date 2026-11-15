# Pipeline observation frontend

Same-origin, dependency-free ES modules for configured pipeline snapshot reads.
No default credential, upstream writes, logs, provider polling or production
startup. [Service integration](../../packages/pipeline_ui/README.md) ·
[Synthetic acceptance](tests/README.md)

- `entry.mjs`: standalone bearer client creation only, not served by the cookie host.
- `runs.mjs`: explicit-client controller shared by standalone and cookie pages.
- [Cookie-only entry](../../packages/application_ui/README.md): existing session
  authority, scoped GET only, no credential conversion or extra provider rights.
