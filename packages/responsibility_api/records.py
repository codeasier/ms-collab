# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Versioned dedicated storage in the existing policy SQLite transaction."""
from dataclasses import dataclass,asdict
import re
import hashlib
from packages.community_board.model import Scope
from packages.community_api.records import read as community_read,encode,decode,fingerprint
from packages.responsibility import fields,PathEvidence
from packages.responsibility.core import PRINCIPAL_ID
from packages.workspaces import SQLiteWorkspaceService,StorageError,PolicyError
from packages.workspaces.authority import capture_authority,validate_authority,authority_current,principal_pin

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


_MAX=9007199254740991
_HISTORY_LIMIT=3000


def _integer(value,minimum=1):
    if type(value) is not int or not minimum<=value<=_MAX:raise ValueError('invalid_revision')
    return value


def _scope(value,scope):
    if type(value) is not dict or value!=scope.as_dict():raise ValueError('invalid_scope')


def _recipients(config):return set(config.reader_ids+config.editor_ids+config.publisher_ids)


def _references(config):
    return {ident for field in config for ident in field['owner_ids']}|{
        field['backup']['principal_id'] for field in config if field['backup'] is not None}


def _eligible(policy,scope,ids):
    for ident in ids:
        principal=policy._principals.get(ident);member=policy._memberships.get((scope.workspace_id,ident))
        if principal is None or not principal.active or member is None or member.state!='active':
            raise PolicyError(400,'reference_not_current_member')
        _integer(member.revision)


def _history(policy,scope,ids):
    _eligible(policy,scope,ids)
    return {'authority':capture_authority(policy,ids),
            'memberships':{ident:policy._memberships[(scope.workspace_id,ident)].revision for ident in sorted(ids)}}


def _validate_history(value,refs,realm):
    if type(value) is not dict or set(value)!={'authority','memberships'} or type(value['memberships']) is not dict:
        raise ValueError('invalid_reference_history')
    ids=set(value['memberships'])
    if (len(ids)>_HISTORY_LIMIT or not refs<=ids
            or any(type(ident) is not str or not PRINCIPAL_ID.fullmatch(ident) for ident in ids)):
        raise ValueError('invalid_reference_history')
    validate_authority(value['authority'],ids)
    if value['authority']['realm']!=realm:raise ValueError('foreign_reference_realm')
    for revision in value['memberships'].values():_integer(revision)


def policy_text(config,authority,grant_revision):
    validate_authority(authority,_recipients(config));_integer(grant_revision)
    return encode({'version':2,'policy':config.as_dict(),'authority':authority,'grant_revision':grant_revision})


def fields_text(scope,config,history):
    normalized=fields(config)
    _validate_history(history,_references(normalized),history['authority']['realm'])
    return encode({'version':2,'scope':scope.as_dict(),'fields':normalized,'history':history})


def evidence_text(scope,observations,community_revision,observed_at,provenance):
    return encode({'version':2,'scope':scope.as_dict(),'records':normalize_evidence(observations),
                   'community_revision':community_revision,'observed_at':observed_at,'provenance':provenance})


def _validate_provenance(value,realm,grant):
    if (type(value) is not dict or set(value)!={'actor','authority','membership_revision','grant_revision'}
            or type(value['actor']) is not str or not PRINCIPAL_ID.fullmatch(value['actor'])):
        raise ValueError('invalid_publisher_provenance')
    validate_authority(value['authority'],(value['actor'],))
    if value['authority']['realm']!=realm:raise ValueError('foreign_publisher_realm')
    _integer(value['membership_revision']);_integer(value['grant_revision'])
    if value['grant_revision']>grant:raise ValueError('future_publisher_grant')


def read(db,scope,*,allow_legacy=False):
    """Strict all-bound reads; all-legacy is ONLY for explicit destructive reset."""
    check_schema(db)
    row=db.execute(f'SELECT policy,fields,config_version,evidence,community_revision,observed_at,revision FROM {_TABLE} WHERE workspace_id=? AND connection_id=? AND repository=?',
                   (scope.workspace_id,scope.connection_id,scope.repository)).fetchone()
    if row is None:return None
    try:
        _integer(row[2]);_integer(row[4],0);_integer(row[6])
        if row[2]>row[6]:raise ValueError('future_configuration')
        from packages.community_board.model import utc_time
        if row[5] is not None:utc_time(row[5])
        if bool(row[4])!=(row[5] is not None):raise ValueError('invalid_observation')
        p,c,e=(decode(row[i]) for i in (0,1,3))
        # No mixed upgrade, missing-history fallback or legacy exposure.
        legacy=(type(p) is dict and set(p)=={'scope','reader_ids','editor_ids','publisher_ids'}
                and type(c) is list and type(e) is list)
        authority=history=provenance=None;grant=0
        if legacy:
            if not allow_legacy:raise ValueError('explicit_regrant_required')
            config=ResponsibilityPolicy.from_dict(p);normalized=fields(c);observations=normalize_evidence(e)
            if config.scope!=scope or any(encode(v)!=row[i] for v,i in ((config.as_dict(),0),(normalized,1),(observations,3))):
                raise ValueError('invalid_legacy_record')
        else:
            if (type(p) is not dict or set(p)!={'version','policy','authority','grant_revision'}
                    or type(p['version']) is not int or p['version']!=2):raise ValueError('invalid_policy_envelope')
            config=ResponsibilityPolicy.from_dict(p['policy']);authority=p['authority'];grant=_integer(p['grant_revision'])
            if config.scope!=scope or grant>row[6] or policy_text(config,authority,grant)!=row[0]:raise ValueError('invalid_policy_envelope')
            if type(c) is not dict or set(c)!={'version','scope','fields','history'} or type(c['version']) is not int or c['version']!=2:raise ValueError('invalid_fields_envelope')
            _scope(c['scope'],scope);normalized=fields(c['fields']);history=c['history']
            _validate_history(history,_references(normalized),authority['realm'])
            if fields_text(scope,normalized,history)!=row[1]:raise ValueError('invalid_fields_envelope')
            if (type(e) is not dict or set(e)!={'version','scope','records','community_revision','observed_at','provenance'}
                    or type(e['version']) is not int or e['version']!=2):raise ValueError('invalid_evidence_envelope')
            _scope(e['scope'],scope);_integer(e['community_revision'],0)
            if e['community_revision']!=row[4] or e['observed_at']!=row[5]:raise ValueError('evidence_metadata_mismatch')
            observations=normalize_evidence(e['records']);provenance=e['provenance']
            if row[4]:_validate_provenance(provenance,authority['realm'],grant)
            elif provenance is not None:raise ValueError('unexpected_provenance')
            if evidence_text(scope,observations,row[4],row[5],provenance)!=row[3]:raise ValueError('invalid_evidence_envelope')
        if not row[4] and observations:raise ValueError('unpublished_evidence')
        return dict(policy=config,authority=authority,grant_revision=grant,legacy=legacy,history=history,
                    provenance=provenance,policy_raw=row[0],fields_raw=row[1],fields=normalized,
                    config_version=row[2],evidence=observations,community_revision=row[4],observed_at=row[5],revision=row[6])
    except (ValueError,TypeError,KeyError,RecursionError,OverflowError):raise StorageError('invalid_responsibility_data') from None


def stale_references(policy,state):
    scope=state['policy'].scope;history=state['history']
    return sorted(ident for ident in _references(state['fields']) if not reference_current(policy,scope,history,ident))


def reference_current(policy,scope,history,ident):
    """Internal validated-history check; avoid quadratic whole-map validation."""
    principal=policy._principals.get(ident);member=policy._memberships.get((scope.workspace_id,ident))
    binding=history['authority']['principals'].get(ident)
    return (principal is not None and principal.active and history['authority']['realm']==policy._realm
            and binding=={'issuer':principal.issuer,'subject':principal.subject,'generation':principal.generation}
            and member is not None and member.state=='active'
            and member.revision==history['memberships'].get(ident))


def updated_fields(policy,state,config):
    scope=state['policy'].scope;config=fields(config);refs=_references(config)
    if not {f['id'] for f in state['fields']}<={f['id'] for f in config}:raise PolicyError(409,'field_removal_not_supported')
    _eligible(policy,scope,refs)
    history=decode(encode(state['history']))
    fresh=refs-set(history['memberships'])
    if len(set(history['memberships'])|fresh)>_HISTORY_LIMIT:raise PolicyError(409,'reference_history_exhausted')
    if fresh:
        added=_history(policy,scope,fresh)
        history['authority']['principals'].update(added['authority']['principals'])
        history['memberships'].update(added['memberships'])
    if any(not reference_current(policy,scope,history,ident) for ident in refs):raise PolicyError(409,'reference_reconfirmation_required')
    return fields_text(scope,config,history)


def proposal(policy,actor,state,community):
    """Current authorized-editor proposal precondition, never a capability."""
    scope=state['policy'].scope
    current=_history(policy,scope,_references(state['fields']))
    value={'version':1,'realm':policy._realm,'scope':scope.as_dict(),'revision':state['revision'],
           'config_version':state['config_version'],'grant_revision':state['grant_revision'],
           'fields_digest':hashlib.sha256(encode(state['fields']).encode('ascii')).hexdigest(),
           'history_digest':hashlib.sha256(state['fields_raw'].encode('ascii')).hexdigest(),
           'targets_digest':hashlib.sha256(encode(current).encode('ascii')).hexdigest(),'actor':principal_pin(policy,actor),
           'member_revision':policy._memberships[(scope.workspace_id,actor)].revision,
           'workspace_revision':policy._workspaces[scope.workspace_id].revision,
           'module_revisions':[policy._modules[(scope.workspace_id,k)].revision for k in ('responsibility','community')],
           'community_config_digest':hashlib.sha256(community.config_raw.encode('ascii')).hexdigest()}
    for revision in (value['member_revision'],value['workspace_revision'],*value['module_revisions']):_integer(revision)
    return hashlib.sha256(encode(value).encode('ascii')).hexdigest(),current


def reconfirmed_fields(state,current):
    history=decode(encode(state['history']))
    history['authority']['principals'].update(current['authority']['principals'])
    history['memberships'].update(current['memberships'])
    return fields_text(state['policy'].scope,state['fields'],history)


def capture_publisher(policy,actor,state):
    scope=state['policy'].scope;member=policy._memberships[(scope.workspace_id,actor)]
    _integer(member.revision)
    return {'actor':actor,'authority':capture_authority(policy,(actor,)),
            'membership_revision':member.revision,'grant_revision':state['grant_revision']}


def evidence_current(policy,state,community):
    proof=state['provenance'];scope=state['policy'].scope
    if proof is None or state['community_revision']!=community.revision:return False
    actor=proof['actor'];member=policy._memberships.get((scope.workspace_id,actor))
    return (proof['grant_revision']==state['grant_revision'] and authority_current(policy,actor,proof['authority'])
        and authority_current(policy,actor,state['authority']) and authority_current(policy,actor,community.authority)
        and actor in state['policy'].reader_ids and actor in state['policy'].publisher_ids and actor in community.policy.reader_ids
        and member is not None and member.state=='active' and member.role=='admin' and member.revision==proof['membership_revision'])


def writable(state,*,config=False,grant=False):
    if (state['revision']>=_MAX or config and state['config_version']>=_MAX
            or grant and state['grant_revision']>=_MAX):raise PolicyError(409,'responsibility_revision_exhausted')


def audit(policy,actor,scope,revision,action='resource.update'):
    # Keep the reviewed shared action vocabulary; distinguish local operations
    # with a fixed value-free object prefix, never fields or principal claims.
    prefix='responsibility' if action=='resource.update' else action
    policy._changed(actor,scope.workspace_id,'resource.update',prefix+':'+hashlib.sha256(encode(scope.as_dict()).encode('ascii')).hexdigest(),revision)


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
            _eligible(policy,scope,_recipients(item))
            db.execute(f'INSERT INTO {_TABLE} VALUES (?,?,?,?,?,?,?,?,?,?)',
                (scope.workspace_id,scope.connection_id,scope.repository,policy_text(item,capture_authority(policy,_recipients(item)),1),
                 fields_text(scope,[],_history(policy,scope,set())),1,evidence_text(scope,[],0,None,None),0,None,1))
    store._transaction(setup,write=True,with_connection=True)


def replace_policy(store,new_policy,*,actor_id,expected_revision):
    """Trusted, explicit destructive regrant; actor_id is NOT authentication.

    Never expose this helper to client actor claims. The host must authenticate
    its administrator before calling. No old responsibility ACL is adopted.
    """
    if type(store) is not SQLiteWorkspaceService or type(new_policy) is not ResponsibilityPolicy:raise ValueError('invalid_policy')
    def replace(policy,db):
        from packages.community_api import CommunityApplication
        scope=new_policy.scope
        policy._admin(actor_id,scope.workspace_id)
        policy.authorize(actor_id,scope.workspace_id,'responsibility','write')
        policy.authorize(actor_id,scope.workspace_id,'responsibility','read')
        CommunityApplication._state(policy,actor_id,db,scope,'items')
        old=read(db,scope,allow_legacy=True)
        if old is None:raise PolicyError(404,'not_found')
        policy._match(expected_revision,old['revision']);writable(old,config=True,grant=True)
        _eligible(policy,scope,_recipients(new_policy))
        raw=policy_text(new_policy,capture_authority(policy,_recipients(new_policy)),old['grant_revision']+1)
        db.execute(f'UPDATE {_TABLE} SET policy=?,fields=?,evidence=?,community_revision=0,observed_at=NULL,config_version=config_version+1,revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=?',
                   (raw,fields_text(scope,[],_history(policy,scope,set())),evidence_text(scope,[],0,None,None),scope.workspace_id,scope.connection_id,scope.repository))
        audit(policy,actor_id,scope,old['revision']+1,'responsibility.grants.reset')
    store._transaction(replace,write=True,with_connection=True)
