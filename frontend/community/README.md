# Authorized community board

Static, dependency-free UI for the composed authorized community service. This
is independent of `frontend/index.html` and its synthetic read-only demo.
Read the [wrapper contract](../../packages/community_ui/README.md) before use.

The controller is exported as `mountCommunityPage` for explicit composition. Standalone `entry.mjs` alone creates its legacy memory-only bearer client. The [unified session entry](../../packages/application_ui/README.md#community-sharing-boundary) injects a cookie-only client with no credential form or conversion. Both modes keep the same complete-snapshot approval, confirmation and scope/version semantics.
