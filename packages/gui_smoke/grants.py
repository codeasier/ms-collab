# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Persistent exact-scope grants. Loading never captures current identities."""
from dataclasses import dataclass
from hashlib import sha256

from packages.workspaces import PolicyError, StorageError
from packages.workspaces.authority import validate_authority
from .model import MAX_REVISION, SmokeScope, decode, encode, exact, identifier, integer

_SCHEMA = f"""CREATE TABLE gui_smoke_grants (
 workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='gui_smoke'),
 connection_id TEXT NOT NULL, repository_id TEXT NOT NULL,
 grant_revision INTEGER NOT NULL CHECK(grant_revision>=1 AND grant_revision<={MAX_REVISION}),
 snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision>=0 AND snapshot_revision<={MAX_REVISION}),
 record TEXT NOT NULL, PRIMARY KEY(workspace_id,connection_id,repository_id),
 FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT"""


@dataclass(frozen=True)
class SmokeConnection:
    scope: SmokeScope
    readers: frozenset[str]
    publishers: frozenset[str]

    def __post_init__(self):
        if type(self.scope) is not SmokeScope:
            raise ValueError('explicit_scope_required')
        for people in (self.readers,self.publishers):
            if type(people) is not frozenset or not 1 <= len(people) <= 100:
                raise ValueError('explicit_audience_required')
            for person in people: identifier(person)


@dataclass(frozen=True)
class Grant:
    configured: SmokeConnection
    revision: int
    snapshot_revision: int
    authority: dict
    raw: str

    @property
    def digest(self):
        return sha256(self.raw.encode('ascii')).hexdigest()


def key(scope):
    return scope.workspace_id,scope.connection_id,scope.repository_id


def recipients(configured):
    return configured.readers | configured.publishers


def grant_text(configured, authority):
    validate_authority(authority,recipients(configured))
    return encode({'version':1,'scope':configured.scope.as_dict(),
                   'readers':sorted(configured.readers),'publishers':sorted(configured.publishers),
                   'authority':authority})


def validate_schema(connection):
    row=connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name='gui_smoke_grants'").fetchone()
    if row is None or type(row[0]) is not str or ' '.join(row[0].split())!=' '.join(_SCHEMA.split()):
        raise StorageError('invalid_gui_smoke_schema')


def load_grant(connection, scope, *, required=True):
    validate_schema(connection)
    row=connection.execute('SELECT grant_revision,snapshot_revision,record FROM gui_smoke_grants WHERE workspace_id=? AND connection_id=? AND repository_id=?',key(scope)).fetchone()
    if row is None:
        if required: raise PolicyError(404,'not_found')
        return None
    try:
        revision,snapshot_revision,raw=row
        integer(revision,1,MAX_REVISION);integer(snapshot_revision,0,MAX_REVISION)
        record=decode(raw);exact(record,{'version','scope','readers','publishers','authority'})
        if type(record['version']) is not int or record['version']!=1 or record['scope']!=scope.as_dict():raise ValueError()
        if any(type(record[k]) is not list for k in ('readers','publishers')):raise ValueError()
        configured=SmokeConnection(scope,frozenset(record['readers']),frozenset(record['publishers']))
        if grant_text(configured,record['authority'])!=raw:raise ValueError()
        return Grant(configured,revision,snapshot_revision,record['authority'],raw)
    except (ValueError,TypeError,KeyError,UnicodeError,RecursionError,OverflowError):
        raise StorageError('invalid_gui_smoke_grant') from None


def save_grant(connection, configured, raw, revision, snapshot_revision, *, prior=None):
    if prior is None:
        connection.execute("INSERT INTO gui_smoke_grants VALUES (?,'gui_smoke',?,?,?,?,?)",(*key(configured.scope),revision,snapshot_revision,raw))
    else:
        result=connection.execute('UPDATE gui_smoke_grants SET grant_revision=?,snapshot_revision=?,record=? WHERE workspace_id=? AND connection_id=? AND repository_id=? AND grant_revision=? AND snapshot_revision=? AND record=?',
            (revision,snapshot_revision,raw,*key(configured.scope),prior.revision,prior.snapshot_revision,prior.raw))
        if result.rowcount!=1:raise PolicyError(412,'stale_revision')
