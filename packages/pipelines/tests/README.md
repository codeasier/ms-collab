# Pipeline core tests

`python3 -m unittest discover -s packages/pipelines/tests -v`

Synthetic tests cover status/conclusion ambiguity, explicit timezone handling,
composite scope and bounded inputs, duplicate run/job identities, selection
windows, malformed/missing details, successful sibling ambiguity, chronological
statistics and output allowlisting. No tests call a provider, access credentials,
start a server, load old fixtures or change external state.

[Return to the package](../README.md)
