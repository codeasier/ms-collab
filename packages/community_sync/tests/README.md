# Snapshot adapter tests

Run `python3 -m unittest discover -s packages/community_sync/tests -v` from the
repository root. All responses and credentials are synthetic; network opening is
mocked. Temporary SQLite files are removed after each test.

- [Test cases](test_sync.py)
- [Adapter contract](../README.md)
