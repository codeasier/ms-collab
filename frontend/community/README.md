# Authorized community board

Static, dependency-free UI for the composed authorized community service. This
is independent of `frontend/index.html` and its synthetic read-only demo.
Read the [wrapper contract](../../packages/community_ui/README.md) before use.

The controller is exported as `mountCommunityPage` for explicit composition. Standalone `entry.mjs` alone creates its legacy memory-only bearer client. The [unified session entry](../../packages/application_ui/README.md#community-sharing-boundary) injects a cookie-only client with no credential form or conversion. Both modes keep the same complete-snapshot approval, confirmation and scope/version semantics.

### Explore approved labels

In **Shared items**, labels describe only the approved, loaded observation in the
current scope, not the repository as a whole. The controls appear after a complete,
available shared read of at most 200 items; private review and refresh-only access
never supply labels. A shared response carrying private review fields is not used
for these controls. Reload to recheck access and content; local filtering makes
no network request and does not check permissions again.

Apply the ordinary kind, state and title-or-label filters first. Counts span all
of their matching items, not just the visible page: a repeated label on one issue
or pull request counts once. The ten most frequent labels appear first; ties use
UTF-16 code-unit order. **Next labels** browses the remaining ranks ten at a time.
Pick a label, or type its original single-line value to match it exactly. Case,
spaces and Unicode spelling are significant. Quotation marks and escaping in the
display distinguish literal text from whitespace or directional controls; use the
ranked buttons for labels with line breaks. Unknown values match nothing. **Clear
exact label** keeps the ordinary filters and returns to the first item page.
Label choices are local to this loaded view, never stored or added to a URL or
request, and are discarded when the snapshot is cleared or reloaded.
