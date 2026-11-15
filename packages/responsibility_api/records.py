# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Versioned dedicated storage in the existing policy SQLite transaction."""
from dataclasses import dataclass,asdict
import re
from packages.community_board.model import Scope
from packages.community_api.records import read as community_read,encode,decode,fingerprint
from packages.responsibility import fields,PathEvidence
from packages.responsibility.core import PRINCIPAL_ID
from packages.workspaces import SQLiteWorkspaceService,StorageError

_TABLE='responsibility_connections_v1'
_SCHEMA=f"""CREATE TABLE {_TABLE} (
 workspace_id TEXT NOT NULL,connection_id TEXT NOT NULL,repository TEXT NOT NULL,
 policy TEXT NOT NULL,fields TEXT NOT NULL,config_version INTEGER NOT NULL CHECK(config_version>=1),
 evidence TEXT NOT NULL,community_revision INTEGER NOT NULL CHECK(community_revision>=0),
 observed_at TEXT,revision INTEGER NOT NULL CHECK(revision>=1),
 PRIMARY KEY(workspace_id,connection_id,repository),
 FOREIGN KEY(workspace_id,connection_id,repository) REFERENCES community_connections(workspace_id,connection_id,repository)
) STRICT"""
_HASH=re.compile(r'[0-9a-f]{64}\Z')


@dataclass(frozen=True)
class ResponsibilityPolicy:
    scope:Scope
    reader_ids:tuple[str,...]
    editor_ids:tuple[str,...]
    publisher_ids:tuple[str,...]

    def __post_init__(self):
        if type(self.scope) is not Scope:raise ValueError('invalid_scope')
        for ids in (self.reader_ids,self.editor_ids,self.publisher_ids):
            if type(ids) is not tuple or len(ids)>1000 or any(type(p) is not str or not PRINCIPAL_ID.fullmatch(p) for p in ids) or len(set(ids))!=len(ids):
                raise ValueError('invalid_grants')

    def as_dict(self):return asdict(self)

    @classmethod
    def from_dict(cls,value):
        if type(value) is not dict or set(value)!={'scope','reader_ids','editor_ids','publisher_ids'}:raise ValueError('invalid_policy')
        if type(value['scope']) is not dict or set(value['scope'])!={'workspace_id','connection_id','repository'}:raise ValueError('invalid_scope')
        if any(type(value[k]) is not list for k in ('reader_ids','editor_ids','publisher_ids')):raise ValueError('invalid_grants')
        return cls(Scope(**value['scope']),*(tuple(value[k]) for k in ('reader_ids','editor_ids','publisher_ids')))


def check_schema(db):
    row=db.execute('SELECT type,sql FROM sqlite_schema WHERE name=?',(_TABLE,)).fetchone()
    normalize=lambda s:re.sub(r'\s+',' ',s).strip()
    if row is None or row[0]!='table' or type(row[1]) is not str or normalize(row[1])!=normalize(_SCHEMA):
        raise StorageError('invalid_responsibility_schema')


def normalize_evidence(value):
    if type(value) is not list or len(value)>200:raise ValueError('invalid_observations')
    result=[];seen=set()
    for item in value:
        if type(item) is not dict or set(item)!={'fingerprint','paths'} or type(item['fingerprint']) is not str or not _HASH.fullmatch(item['fingerprint']) or item['fingerprint'] in seen:
            raise ValueError('invalid_observation')
        result.append({'fingerprint':item['fingerprint'],'paths':PathEvidence.from_dict(item['paths']).as_dict()});seen.add(item['fingerprint'])
    encode(result) # Shared explicit 1 MiB store bound, no silent truncation.
    return result


def read(db,scope):
    check_schema(db)
    row=db.execute(f'SELECT policy,fields,config_version,evidence,community_revision,observed_at,revision FROM {_TABLE} WHERE workspace_id=? AND connection_id=? AND repository=?',
                   (scope.workspace_id,scope.connection_id,scope.repository)).fetchone()
    if row is None:return None
    try:
        policy=ResponsibilityPolicy.from_dict(decode(row[0]))
        if policy.scope!=scope or type(row[2]) is not int or row[2]<1 or type(row[4]) is not int or row[4]<0 or type(row[6]) is not int or row[6]<1:raise ValueError()
        config=fields(decode(row[1]));evidence=normalize_evidence(decode(row[3]))
        from packages.community_board.model import utc_time
        if row[5] is not None:utc_time(row[5])
        if bool(row[4])!=(row[5] is not None):raise ValueError()
        if not row[4] and evidence:raise ValueError()
        return dict(policy=policy,fields=config,config_version=row[2],evidence=evidence,community_revision=row[4],observed_at=row[5],revision=row[6])
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):raise StorageError('invalid_responsibility_data') from None


def initialize(store,policies):
    if type(store) is not SQLiteWorkspaceService:raise ValueError('initialized_policy_store_required')
    policies=tuple(policies)
    if any(type(p) is not ResponsibilityPolicy for p in policies):raise ValueError('invalid_policy')
    def setup(policy,db):
        db.execute(_SCHEMA)
        for item in policies:
            scope=item.scope
            if (scope.workspace_id,'responsibility') not in policy._modules:raise ValueError('missing_responsibility_module')
            if community_read(db,scope) is None:raise ValueError('missing_community_connection')
            if not set(item.reader_ids+item.editor_ids+item.publisher_ids)<=policy._principals.keys():raise ValueError('unknown_principal')
            db.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?)',
                       (scope.workspace_id,scope.connection_id,scope.repository,encode(item.as_dict()),'[]',1,'[]',0,None,1))
    store._transaction(setup,write=True,with_connection=True)


def replace_policy(store,new_policy,*,expected_revision):
    """Trusted integration only. Audience changes clear prior config and evidence."""
    if type(store) is not SQLiteWorkspaceService or type(new_policy) is not ResponsibilityPolicy:raise ValueError('invalid_policy')
    def replace(policy,db):
        scope=new_policy.scope;old=read(db,scope)
        if old is None:raise ValueError('unknown_scope')
        policy._match(expected_revision,old['revision'])
        if not set(new_policy.reader_ids+new_policy.editor_ids+new_policy.publisher_ids)<=policy._principals.keys():raise ValueError('unknown_principal')
        db.execute(f'UPDATE {_TABLE} SET policy=?,fields=\'[]\',evidence=\'[]\',community_revision=0,observed_at=NULL,config_version=config_version+1,revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=?',
                   (encode(new_policy.as_dict()),scope.workspace_id,scope.connection_id,scope.repository))
    store._transaction(replace,write=True,with_connection=True)
