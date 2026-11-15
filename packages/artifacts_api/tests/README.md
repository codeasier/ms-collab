# Artifact API tests

Purely synthetic temporary SQLite metadata and policy records. The credential
map is a test double only, never an application provider. Includes in-process
WSGI and real loopback HTTP tests; no upstream or production connection.

Run `python3 -m unittest discover -s packages/artifacts_api/tests -v` from the
repository root. [API and limitations](../README.md).
