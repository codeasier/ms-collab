# Download acceptance tests

[Package contract](../README.md)

Run `python3 -m unittest discover -s packages/artifact_download/tests -v` from the
repository root. Inputs are synthetic, including all identities, signed material,
object bytes and OBS protocol responses. No cloud account or real object is read.

- [Authorization, persistent admission, real HTTP and stream lifecycle](test_download.py)
- [Signing, response shape, DNS/TLS, deadline and binding bounds](test_object_transport.py)

A focused pass does not replace the complete repository checks or real-provider
acceptance. There is no frontend change in this slice and no screenshot is
presented as evidence of cloud downloads.
