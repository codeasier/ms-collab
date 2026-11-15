# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Explicit installation, read-only checks, maintenance lease and WSGI factory."""
from contextlib import closing
from datetime import datetime, timezone, timedelta
from pathlib import Path
import fcntl
import json
import os
import sqlite3
from urllib.parse import quote

from packages.application_host import ApplicationHost, HostConfig
from packages.application_ui import ApplicationUI
from packages.login_state import SQLiteLoginStore
from packages.session_api import SessionPolicyStore
from packages.workspaces import SQLiteWorkspaceService
from packages.workspace_admin_api import ModuleTemplate
from .config import RuntimeFailure, absolute, private, read_private, decode, load_config
from .schema import MODULE_SCHEMA, POLICY_DIGEST, LOGIN_DIGEST, schema_digest, _APPLICATION_ID, _SCHEMA_VERSION, _APP, _VERSION

MAX_DB = 256 * 1024 * 1024
POLICY = 'policy.sqlite'
LOGIN = 'login.sqlite'
LOCK = '.runtime.lock'
MANIFEST = 'installation.json'


def now_micros():
    return (datetime.now(timezone.utc) - datetime(1970,1,1,tzinfo=timezone.utc)) // timedelta(microseconds=1)


def write_new(path, content):
    fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL | os.O_NOFOLLOW, 0o600)
    with os.fdopen(fd, 'wb') as stream:
        stream.write(content); stream.flush(); os.fsync(stream.fileno())


def canonical(value):
    return json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(',', ':')).encode('ascii')


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
    try: os.fsync(fd)
    finally: os.close(fd)


def allocate_directory(path):
    private(path.parent, directory=True)
    try: os.mkdir(path, 0o700)
    except FileExistsError: raise RuntimeFailure('destination_exists') from None
    except OSError: raise RuntimeFailure('unsafe_filesystem') from None
    private(path, directory=True)


class Lease:
    def __init__(self, directory, *, exclusive=False):
        self.fd = None
        private(directory, directory=True); private(directory / LOCK)
        try:
            fd = os.open(directory / LOCK, os.O_RDONLY | os.O_NOFOLLOW)
            try: fcntl.flock(fd, (fcntl.LOCK_EX if exclusive else fcntl.LOCK_SH) | fcntl.LOCK_NB)
            except BaseException:
                os.close(fd); raise
            self.fd = fd
            self.directory, self.info = directory, os.fstat(fd)
        except OSError:
            raise RuntimeFailure('runtime_busy') from None
    def valid(self):
        if self.fd is None: return False
        try:
            other = private(self.directory / LOCK)
            private(self.directory, directory=True)
            return (other.st_dev, other.st_ino) == (self.info.st_dev, self.info.st_ino)
        except RuntimeFailure: return False
    def close(self):
        if self.fd is not None:
            os.close(self.fd); self.fd = None
    def __enter__(self): return self
    def __exit__(self, *_): self.close()
    def __del__(self): self.close()


def manifest(config):
    return {'format': 1, 'binding': config.binding.fingerprint(), 'policy_schema': POLICY_DIGEST, 'login_schema': LOGIN_DIGEST}


def connection(directory, *, writable=False):
    # Two attached files must be on the same local filesystem. Network filesystem
    # and uncooperative/hostile filesystem writers are not made safe by flock.
    a, b = private(directory / POLICY), private(directory / LOGIN)
    if a.st_dev != b.st_dev or (a.st_dev,a.st_ino) == (b.st_dev,b.st_ino) or max(a.st_size,b.st_size) > MAX_DB:
        raise RuntimeFailure('invalid_storage')
    if any((directory / (name + suffix)).exists() for name in (POLICY,LOGIN) for suffix in ('-wal','-shm')):
        raise RuntimeFailure('invalid_storage')
    mode = 'rw' if writable else 'ro'
    uri = lambda path: 'file:' + quote(str(path), safe='/') + '?mode=' + mode
    db = sqlite3.connect(uri(directory / POLICY), uri=True, timeout=2, isolation_level=None)
    try:
        db.execute('PRAGMA trusted_schema=OFF'); db.execute('PRAGMA foreign_keys=ON')
        db.execute('ATTACH DATABASE ? AS login', (uri(directory / LOGIN),))
        db.execute('BEGIN EXCLUSIVE' if writable else 'BEGIN')
        return db
    except BaseException:
        db.close(); raise


def inspect(config, db):
    stored = decode(read_private(config.directory / MANIFEST, 4096))
    if stored != manifest(config): raise RuntimeFailure('unsupported_layout')
    for name, app, version, expected in (('main',_APPLICATION_ID,_SCHEMA_VERSION,POLICY_DIGEST),('login',_APP,_VERSION,LOGIN_DIGEST)):
        # Recheck the snapshot size under SQLite locks, not only an earlier stat.
        size = db.execute(f'PRAGMA {name}.page_count').fetchone()[0] * db.execute(f'PRAGMA {name}.page_size').fetchone()[0]
        if not 0 < size <= MAX_DB:
            raise RuntimeFailure('invalid_storage')
        if (db.execute(f'PRAGMA {name}.journal_mode').fetchone()[0] != 'delete'
                or db.execute(f'PRAGMA {name}.application_id').fetchone()[0] != app
                or db.execute(f'PRAGMA {name}.user_version').fetchone()[0] != version
                or db.execute(f'PRAGMA {name}.quick_check').fetchall() != [('ok',)]
                or db.execute(f'PRAGMA {name}.foreign_key_check').fetchall()
                or schema_digest(db, name) != expected):
            raise RuntimeFailure('invalid_storage')
    row = db.execute('SELECT binding,clock FROM login.metadata WHERE id=1').fetchone()
    if row is None or row[0] != config.binding.fingerprint() or type(row[1]) is not int or not 0 <= row[1] <= now_micros():
        raise RuntimeFailure('invalid_storage')
    # Full current policy model validation; read-only, no epoch/time update.
    policy = object.__new__(SQLiteWorkspaceService)
    policy._load(db)
    return row[1]


def initialize(config_path):
    config = load_config(config_path)
    allocate_directory(config.directory)
    try:
        write_new(config.directory / LOCK, b'')
        with Lease(config.directory, exclusive=True):
            policy = SQLiteWorkspaceService.initialize(config.directory / POLICY, principals=(), workspaces=(), memberships=(), modules=())
            def setup(_, db):
                for sql in MODULE_SCHEMA: db.execute(sql)
            policy._transaction(setup, write=True, with_connection=True)
            SQLiteLoginStore.initialize(config.directory / LOGIN, binding=config.binding)
            write_new(config.directory / MANIFEST, canonical(manifest(config)))
            sync_directory(config.directory); sync_directory(config.directory.parent)
            with closing(connection(config.directory)) as db: inspect(config, db)
        return status(config)
    except Exception:
        # Keep an incomplete new directory; never silently replace/delete/reuse.
        raise RuntimeFailure('initialization_incomplete') from None


def status(config):
    return {'installation_valid': True, 'login_enabled': False, 'live_provider_verified': False,
            'production_ready': False, 'modules': list(config.modules)}


def check(config_path):
    config = load_config(config_path)
    try:
        with Lease(config.directory), closing(connection(config.directory)) as db:
            inspect(config, db)
        return status(config)
    except RuntimeFailure: raise
    except Exception: raise RuntimeFailure('invalid_storage') from None


class RuntimeApplication:
    def __init__(self, app, lease): self.app, self.lease = app, lease
    def __call__(self, environ, start_response):
        if not self.lease.valid():
            start_response('503 Service Unavailable',[('Content-Type','application/json'),('Cache-Control','no-store')])
            return [b'{"error":"runtime_unavailable"}']
        return self.app(environ, start_response)
    def close(self): self.lease.close()


def create_application(config_path):
    config = load_config(config_path)
    lease = None
    try:
        lease = Lease(config.directory)
        with closing(connection(config.directory)) as db: inspect(config, db)
        policy = SQLiteWorkspaceService(config.directory / POLICY)
        login = SQLiteLoginStore(config.directory / LOGIN, binding=config.binding)
        sessions = SessionPolicyStore(policy=policy, login=login)
        keys = tuple(k.replace('-', '_') if k in {'research-assets','gui-smoke'} else k for k in config.modules)
        host = ApplicationHost(config=HostConfig(config.origin, config.modules,
            tuple(ModuleTemplate(k,('read','write')) for k in keys),
            artifact_administration=config.artifact_administration), sessions=sessions)
        return RuntimeApplication(ApplicationUI(host=host), lease)
    except Exception:
        if lease is not None: lease.close()
        raise RuntimeFailure('runtime_unavailable') from None
