"""Small, dependency-free public-content tripwire, not a complete secret scanner."""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path, PurePosixPath

ROOT = Path(__file__).resolve().parents[2]
# Only reviewed, value-free templates may be committed under an environment name.
ENV_TEMPLATES = {".env.example", ".env.template"}
SECRET_MARKERS = (
    re.compile(rb"-----BEGIN (?:RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----"),
    re.compile(rb"\bgh[pousr]_[A-Za-z0-9]{36,255}\b"),
    re.compile(rb"\bgithub_pat_[A-Za-z0-9_]{40,255}\b"),
)


def inspect_file(path: str, content: bytes, *, symlink: bool = False) -> list[str]:
    """Return reasons only, never matching credential text."""
    name = PurePosixPath(path).name.lower()
    parts = PurePosixPath(path).parts
    errors = []
    if symlink:
        errors.append("symlink requires an explicit public-content policy")
    if name.startswith(".env") and name not in ENV_TEMPLATES:
        errors.append("runtime environment file")
    if re.search(r"\.(?:db|sqlite|sqlite3)(?:-wal|-shm|-journal)?$", name):
        errors.append("runtime database")
    if name.endswith((".key", ".p12", ".pfx")) or name in {"id_rsa", "id_ed25519"}:
        errors.append("credential container")
    if any(part in {"node_modules", ".venv", "__pycache__", "runtime", "logs"} for part in parts):
        errors.append("generated or runtime directory")
    if any(pattern.search(content) for pattern in SECRET_MARKERS):
        errors.append("possible credential material (value redacted)")
    return errors


def check(root: Path) -> list[tuple[str, str]]:
    names = subprocess.check_output(
        ["git", "ls-files", "-z"], cwd=root
    ).decode("utf-8").split("\0")
    errors = []
    for name in filter(None, names):
        path = root / name
        if path.is_symlink():
            errors.append((name, "symlink requires an explicit public-content policy"))
            continue  # Never follow a link outside the checkout.
        if not path.is_file():
            errors.append((name, "tracked path is not a regular file"))
            continue
        for reason in inspect_file(name, path.read_bytes()):
            errors.append((name, reason))
    return errors


def main() -> int:
    errors = check(ROOT)
    for path, reason in errors:
        # repr prevents untrusted filenames from injecting multiline log commands.
        print(f"FAIL {path!r}: {reason}")
    if errors:
        print("Public-content checks failed; do not publish sensitive values in logs.")
        return 1
    print("Public-content tripwire passed (not a full security or history audit).")
    return 0


if __name__ == "__main__":
    sys.exit(main())
