# Unified cookie-only frontend

- `shell.mjs`: explicit session inspection/login/logout, fixed navigation and
  pagehide/401 generation fencing; no bearer or default identity.
- `client.mjs`: same-origin cookie requests, fresh readable CSRF proof and pinned
  provider navigation; shared responsibility adapter preserves exact scope/CAS.
- `index.html`, `manage.html`, `responsibility.html`: fixed public shells, no
  credential input or user data embedded in HTML.
- `style.css`: responsive shared navigation/account boundary.
- [Tests](tests/README.md): contract and actual synthetic HTTPS acceptance.

Business controllers are reused from [workspace administration](../workspace-admin/README.md)
and [responsibility](../responsibility/README.md), with an explicit supplied client.
Only their separate legacy `entry.mjs` scripts create bearer clients. New session
pages never import those entry scripts and never copy or fabricate a credential.
The server is [ApplicationUI](../../packages/application_ui/README.md).
