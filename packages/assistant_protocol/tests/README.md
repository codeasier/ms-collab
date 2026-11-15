# Synthetic checks

Run `python3 -m unittest discover -s packages/assistant_protocol/tests -v` on CI.
The fixtures use only memory, temporary SQLite databases and synthetic identities.
No subprocess transport, agent, provider, network, host HOME or production data is
used. The protocol subset has no automatic retry/replay or permitted FS/terminal
callback. Admission tests verify always-blocked output, exact owner and revision,
current dependency/member/session checks, CSRF, idempotency, concurrency, bounded
storage and atomic audit/expiry rollback. They do not certify OS isolation.
