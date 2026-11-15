# Community-board tests

Run from the repository root:

```sh
python3 -m unittest discover -s packages/community_board/tests -v
```

All records are synthetic and in memory. Coverage includes explicit workspace
scopes, connection/repository/item identity collisions, public-space separation,
filters and stable pagination, input/output aliasing, timezone validation,
stale observations and missing-record semantics. These are library contract
tests, not authenticated HTTP, provider integration or browser acceptance.
