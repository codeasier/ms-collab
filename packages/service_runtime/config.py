# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Strict non-secret process configuration, never an identity/grant source."""
from dataclasses import dataclass, field
from pathlib import Path
import json
import os
import stat
from packages.application_host import HostConfig
from packages.login_state import LoginBinding
from packages.session_api.application import _https_url


class RuntimeFailure(Exception):
    def __init__(self, code='invalid_configuration'):
        self.code = code
        super().__init__(code)


def absolute(value):
    if (type(value) is not str or not 1 <= len(value) <= 4096 or '\\' in value
            or any(ord(c) < 32 for c in value) or not value.startswith('/') or value.startswith('//')):
        raise RuntimeFailure()
    path = Path(value)
    if str(path) != value or '..' in path.parts or path == Path('/'):
        raise RuntimeFailure()
    # Trusted local filesystem only. Never follow a configured symlink, including
    # an ancestor; this is not protection against a malicious same-UID operator.
    for item in reversed((path, *path.parents)):
        if item.is_symlink():
            raise RuntimeFailure('unsafe_filesystem')
    return path


def private(path, *, directory=False):
    try:
        info = path.lstat()
        valid = stat.S_ISDIR(info.st_mode) if directory else stat.S_ISREG(info.st_mode)
        if not valid or info.st_uid != os.geteuid() or info.st_mode & 0o077 or (not directory and info.st_nlink != 1):
            raise RuntimeFailure('unsafe_filesystem')
        return info
    except OSError:
        raise RuntimeFailure('unsafe_filesystem') from None


def read_private(path, limit):
    private(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW)
        with os.fdopen(fd, 'rb') as stream:
            info = os.fstat(stream.fileno())
            if not stat.S_ISREG(info.st_mode) or info.st_size > limit or info.st_uid != os.geteuid() or info.st_mode & 0o077 or info.st_nlink != 1:
                raise RuntimeFailure('unsafe_filesystem')
            raw = stream.read(limit + 1)
        if len(raw) > limit:
            raise RuntimeFailure('invalid_configuration')
        return raw
    except OSError:
        raise RuntimeFailure('unsafe_filesystem') from None


def decode(raw):
    def pairs(items):
        data = {}
        for key, value in items:
            if key in data:
                raise ValueError()
            data[key] = value
        return data
    try:
        return json.loads(raw, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, UnicodeError, RecursionError):
        raise RuntimeFailure() from None


@dataclass(frozen=True)
class RuntimeConfig:
    origin: str = field(repr=False)
    directory: Path = field(repr=False)
    modules: tuple[str, ...]
    artifact_administration: bool
    binding: LoginBinding = field(repr=False)


def load_config(path):
    try:
        path = absolute(str(path)); private(path.parent, directory=True)
        raw = decode(read_private(path, 16384))
        if type(raw) is not dict or set(raw) != {'version','origin','data_directory','modules','artifact_administration','login'}:
            raise RuntimeFailure()
        if type(raw['version']) is not int or raw['version'] != 1:
            raise RuntimeFailure()
        login = raw['login']
        if type(login) is not dict or set(login) != {'mode','issuer','audience'} or login['mode'] != 'disabled':
            raise RuntimeFailure('provider_not_enabled')
        _https_url(raw['origin'], origin=True)
        if type(raw['modules']) is not list:
            raise RuntimeFailure()
        modules = tuple(raw['modules'])
        HostConfig(raw['origin'], modules, artifact_administration=raw['artifact_administration'])
        binding = LoginBinding(login['issuer'], login['audience'], raw['origin'] + '/auth/callback')
        return RuntimeConfig(raw['origin'], absolute(raw['data_directory']), modules, raw['artifact_administration'], binding)
    except RuntimeFailure:
        raise
    except Exception:
        raise RuntimeFailure() from None
