# Synthetic GUI observation tests

- [Core saved-observation contract](test_smoke.py): scope, projections, permissions,
  paging, corruption, CAS, rollback and loopback HTTP.
- [Persistent authority](test_authority.py): publisher/reader ABA, enrollment,
  explicit regrant, nonreset counters, migration and independent-process races.

Run `python3 -m unittest discover -s packages/gui_smoke/tests -v` from the repository
root. No real provider, identity mutation or production resources are used.

[Package navigation](../README.md) · [Grant contract](../GRANTS.md)
