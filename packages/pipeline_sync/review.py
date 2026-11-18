# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""One local human review of a bounded, already authorized replacement; no writer.

V1 supports only an owner-private foreground Linux devpts terminal. No stdio,
path, fd, environment variable or UI callback selects the review destination.
The trusted operator must ensure the terminal/master and screen are not shared
or recorded. OS permissions and a typed challenge cannot prove that or humanity.
"""
from contextlib import ExitStack
from dataclasses import dataclass, field
import hashlib
import json
import os
import re
import secrets
import stat
import sys

from packages.pipeline_api import decode_snapshots, encode_snapshots
from packages.workspaces.model import identifier, revision
from .provider import Connection, ProviderError

REVIEW_AUDIENCE = "current-and-future-workspace-pipeline-readers/v1"
_PROJECTION = "pipeline-snapshot-content-and-audience-review/v1"


@dataclass(frozen=True, slots=True, kw_only=True)
class PipelineReviewPolicy:
    """Explicit trusted composition, neither a credential nor a current grant."""
    connection: Connection
    reviewer_pin: tuple = field(repr=False)
    reviewer_epoch: int
    membership_revision: int
    audience: str

    def __post_init__(self):
        pin = self.reviewer_pin
        if (type(self.connection) is not Connection or type(self.audience) is not str
                or self.audience != REVIEW_AUDIENCE or type(pin) is not tuple or len(pin) != 5
                or type(pin[0]) is not str or not re.fullmatch(r"[0-9a-f]{32}", pin[0])):
            raise ValueError("invalid_pipeline_review_policy")
        for part in pin[1:4]:
            if type(part) is not str:
                raise ValueError("invalid_pipeline_review_policy")
            identifier(part)
            if any(0xD800 <= ord(c) <= 0xDFFF for c in part):
                raise ValueError("invalid_pipeline_review_policy")
        for counter in (pin[4], self.reviewer_epoch, self.membership_revision):
            revision(counter)


def _json(value):
    return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)


def _digest(text):
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _review(connection, policy, version, old_text, new_text):
    if type(connection) is not Connection or type(policy) is not PipelineReviewPolicy or connection != policy.connection:
        raise ValueError("invalid_pipeline_review_policy")
    revision(version)
    scope = connection.scope
    old = decode_snapshots(scope, old_text)
    new = decode_snapshots(scope, new_text)
    if not new:
        raise ProviderError("empty_observation")
    # Never show a normalized approximation of different bytes about to be
    # written. Only the exact canonical projection produced by the V1 collector.
    candidate = encode_snapshots(scope, new)
    if candidate != new_text:
        raise ProviderError("invalid_review_candidate")
    source = {"origin": connection.origin, "run_limit": connection.run_limit,
              "timeout": connection.timeout, "max_response_bytes": connection.max_response_bytes}
    removed = sorted({r["id"] for r in old} - {r["id"] for r in new})
    digest = _digest(_json({
        "projection": _PROJECTION, "nonce": secrets.token_hex(32), "audience": policy.audience,
        "scope": scope.as_dict(), "source": source, "expected_revision": version,
        "previous_utf8_sha256": _digest(old_text), "candidate_utf8_sha256": _digest(candidate),
        "reviewer_pin": policy.reviewer_pin, "reviewer_epoch": policy.reviewer_epoch,
        "membership_revision": policy.membership_revision,
    }))
    # Only formatter-owned newlines and spaces add layout. JSON escapes every
    # user-controlled control or non-ASCII character, retaining the exact keys,
    # order and values of the canonical bytes bound above.
    formatted = json.dumps(json.loads(candidate), ensure_ascii=True, indent=2, allow_nan=False)
    # Do not show issuer/subject, credentials, errors or unprojected provider material.
    preview = "\n".join((
        "Review proposed replacement of the stored pipeline subset.",
        "Scope: " + _json(scope.as_dict()), "Source connection: " + _json(source),
        f"Replace published revision: {version}", f"Old run count: {len(old)}",
        f"New run count: {len(new)}", "Removed run IDs: " + _json(removed),
        "This replaces the current local subset. Omission does not mean deletion from provider history.",
        "An empty replacement is not publishable in this review flow.",
        "Audience: " + policy.audience,
        "These names and records become visible to current and future workspace members with pipeline read access.",
        "This is not per-object authorization. No task is executed and nothing is written upstream.",
        "Candidate (formatted canonical JSON):", formatted, "",
    )).encode("ascii")
    return preview, digest


def _close(fd):
    try:
        os.close(fd)
    except OSError:
        raise ProviderError("review_unavailable") from None


def _private(fd, slave, termios):
    """The alias /dev/tty (5:0) has no useful ownership or privacy bits."""
    import fcntl  # Only reached by the Linux devpts review path.
    info = os.fstat(slave)
    foreground = os.getpgrp()
    iflag, oflag, _, lflag, _, _, cc = termios.tcgetattr(fd)
    # Require the standard Linux N_TTY discipline (int 0). Kernel modes may
    # otherwise change what the reviewer sees or what the prompt receives.
    discipline = fcntl.ioctl(fd, termios.TIOCGETD, bytes(4))
    local_ok = (termios.ICANON | termios.ISIG | termios.IEXTEN | termios.ECHO
                | termios.ECHOE | termios.ECHOK | termios.ECHONL | termios.TOSTOP
                | termios.NOFLSH | termios.ECHOCTL | termios.ECHOPRT | termios.ECHOKE)
    input_rewrite = (termios.IUCLC | termios.ISTRIP | termios.IGNCR | termios.INLCR | termios.PARMRK)
    if (not stat.S_ISCHR(info.st_mode) or info.st_uid != os.geteuid()
            or stat.S_IMODE(info.st_mode) != 0o600
            or os.tcgetpgrp(fd) != foreground or os.tcgetpgrp(slave) != foreground
            or discipline != bytes(4) or not lflag & termios.ICANON or lflag & ~local_ok
            or (oflag & termios.OPOST and oflag & ~(termios.OPOST | termios.ONLCR))
            or iflag & input_rewrite
            # Normal canonical editing is fine; printable/high-bit bytes must
            # not double as editing or flow-control keys. VMIN/VTIME aren't keys.
            or any(ord(key) >= 32 and ord(key) != 127 for i, key in enumerate(cc)
                   if i not in (termios.VMIN, termios.VTIME))):
        raise ProviderError("review_unavailable")


def _terminal(stack):
    if sys.platform != "linux":
        raise ProviderError("review_unavailable")
    try:
        import termios
    except ImportError:
        raise ProviderError("review_unavailable") from None
    try:
        flags = os.O_NOCTTY | os.O_CLOEXEC | os.O_NOFOLLOW
        fd = os.open("/dev/tty", os.O_RDWR | flags)
        stack.callback(_close, fd)
        alias = os.fstat(fd)
        if not stat.S_ISCHR(alias.st_mode) or alias.st_rdev != os.makedev(5, 0):
            raise ProviderError("review_unavailable")

        # Linux proc_pid_stat field 7: kernel device number of *this process's*
        # controlling tty. comm can contain spaces and ')'; split at its end.
        with open("/proc/self/stat", "rb") as proc:
            fields = proc.read(8192).rsplit(b") ", 1)[1].split()
        device = int(fields[4]) & 0xffffffff
        if os.major(device) != 136:  # The supported Unix98 devpts slave class.
            raise ProviderError("review_unavailable")
        slave = os.open(f"/dev/pts/{os.minor(device)}", os.O_RDONLY | os.O_NONBLOCK | flags)
        stack.callback(_close, slave)
        if os.fstat(slave).st_rdev != device:
            raise ProviderError("review_unavailable")
        # tcgetpgrp on the slave also rejects a different devpts namespace's
        # lookalike: Linux requires the calling process's controlling terminal.
        _private(fd, slave, termios)
        return fd, slave, termios
    except (OSError, termios.error, ImportError, AttributeError, IndexError, ValueError):
        raise ProviderError("review_unavailable") from None


def _write(fd, data):
    rest = memoryview(data)
    while rest:
        n = os.write(fd, rest)
        if n <= 0:
            raise ProviderError("review_unavailable")
        rest = rest[n:]


def confirm_replacement(connection, review_policy, expected_revision, old_text, new_text, *, verifier):
    """Display the complete proposal on /dev/tty; return only its confirmed digest.

    verifier is the trusted synchronizer's current-authority check, not a UI or
    approval callback. Its exceptions propagate; successful review is not final
    writer authorization. No actual human or non-recording is inferred.
    """
    if not callable(verifier):
        raise ValueError("review_verifier_required")
    preview, digest = _review(connection, review_policy, expected_revision, old_text, new_text)
    with ExitStack() as stack:
        fd, slave, termios = _terminal(stack)
        if verifier() is not None:
            raise ProviderError("review_unavailable")
        try:
            _private(fd, slave, termios)
            _write(fd, preview)
            # Discard typeahead before issuance. Bound the reply to one exact
            # canonical line, and discard any queued remainder even on refusal.
            termios.tcflush(fd, termios.TCIFLUSH)
            _write(fd, (f"Decision SHA-256: {digest}\n"
                        "Type PUBLISH followed by one space and this digest, then Enter: ").encode("ascii"))
            expected = b"PUBLISH " + digest.encode("ascii") + b"\n"
            try:
                reply = os.read(fd, len(expected) + 1)
            finally:
                termios.tcflush(fd, termios.TCIFLUSH)
            _private(fd, slave, termios)
        except (OSError, termios.error):
            raise ProviderError("review_unavailable") from None
        if reply != expected:
            raise ProviderError("review_not_confirmed")
    return digest
