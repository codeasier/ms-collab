# Authorized community API tests

Run `python3 -m unittest discover -s packages/community_api/tests -v` from the
repository root. Fixtures use test-only identities, a fabricated provider and a
private temporary policy database. No credentials or real project data are used.

- [Authority/regrant/approval/page-boundary tests](test_authority.py)
- [Regression tests](test_application.py)
- [Authorization and sharing contract](../README.md)
