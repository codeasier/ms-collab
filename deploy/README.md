# Generic installation examples

- [Non-secret JSON example](service.example.json): replace fictional coordinates,
  keep `login.mode` exactly `disabled`; no live provider can be enabled here.
- [Pinned server dependency](requirements.txt): official Gunicorn wheel only.
- [Installation, cold backup and upgrades](../packages/service_runtime/README.md).

Examples describe a proposed operator-owned layout, not an existing deployment.
Do not commit runtime configuration, certificates, private keys, databases,
backups, private hostnames or operational records. No system service, firewall,
proxy, trust store or production data is modified by these examples.
