# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Current-user scoped configuration/CAS and approved read-only candidates."""
import hashlib
import json
import re
from datetime import datetime,timezone
from urllib.parse import parse_qsl
from packages.identity import Credential
from packages.community_board.model import Scope,utc_time
from packages.community_api import CommunityApplication
from packages.community_api.records import fingerprint,encode
from packages.responsibility import fields,PathEvidence,project
from packages.responsibility.core import FIELD_ID
from packages.workspace_api import WorkspaceApplication
from packages.workspace_api.application import _object
from packages.workspaces import PolicyError
from packages.workspaces.authority import authority_current
from .records import (_TABLE,read,normalize_evidence,updated_fields,stale_references,proposal,
    reconfirmed_fields,capture_publisher,evidence_text,evidence_current,writable,audit)

_ID=re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,127}\Z')


class ResponsibilityApplication(WorkspaceApplication):
    def __init__(self,*,clock=lambda:datetime.now(timezone.utc),**kwargs):
        super().__init__(clock=clock,**kwargs);self._clock=clock

    @staticmethod
    def _state(policy,actor,db,scope,action):
        # Current community membership/capability/reader grant is necessary for
        # all actions, but never sufficient for responsibility access.
        policy.authorize(actor,scope.workspace_id,'responsibility','read')
        community=CommunityApplication._state(policy,actor,db,scope,'items')
        state=read(db,scope)
        if state is None:raise PolicyError(404,'not_found')
        ids=state['policy'].reader_ids if action=='read' else state['policy'].editor_ids if action=='edit' else state['policy'].publisher_ids
        if (actor not in state['policy'].reader_ids or actor not in ids
                or not authority_current(policy,actor,state['authority'])):raise PolicyError(404,'not_found')
        if action!='read':
            policy._admin(actor,scope.workspace_id)
            policy.authorize(actor,scope.workspace_id,'responsibility','write')
        return state,community

    def _dispatch(self,environ):
        assertion=self._verify_request(environ)
        scope,action,options=self._route(environ)
        expected=self._revision(environ) if action in {'save','reconfirm'} else None
        config=self._config_body(environ,reconfirm=action=='reconfirm') if action in {'save','reconfirm'} else None
        def operation(policy,actor,db):
            state,community=self._state(policy,actor,db,scope,'edit' if action in {'save','reconfirm'} else 'read')
            if action in {'save','reconfirm'}:
                policy._match(expected,state['revision']);writable(state,config=True)
                if action=='save':
                    try:raw=updated_fields(policy,state,config)
                    except ValueError:raise PolicyError(400,'invalid_configuration') from None
                else:
                    desired,token=config
                    if desired!=state['fields']:raise PolicyError(412,'reference_proposal_changed')
                    try:current_token,current=proposal(policy,actor,state,community)
                    except (ValueError,PolicyError):raise PolicyError(409,'reference_reconfirmation_unavailable') from None
                    if token!=current_token:raise PolicyError(412,'reference_proposal_changed')
                    try:raw=reconfirmed_fields(state,current)
                    except ValueError:raise PolicyError(409,'reference_history_exhausted') from None
                self._write(db,scope,'fields=?,config_version=config_version+1',(raw,),state['revision'])
                audit(policy,actor,scope,state['revision']+1,
                      'responsibility.references.reconfirm' if action=='reconfirm' else 'resource.update')
                state=read(db,scope)
            common={'scope':scope.as_dict(),'revision':state['revision'],'config_version':state['config_version'],
                    'identity_namespace':'ms-collab.principal.id','workflow_enabled':False}
            if action=='capabilities':
                editable=True
                try:self._state(policy,actor,db,scope,'edit')
                except PolicyError:editable=False
                return common|{'capabilities':{'read':True,'edit':editable}}
            if action in {'config','save','reconfirm'}:
                stale=stale_references(policy,state);token=None
                try:
                    self._state(policy,actor,db,scope,'edit')
                    token,_=proposal(policy,actor,state,community)
                except (PolicyError,ValueError):pass
                return common|{'fields':state['fields'],'references':{
                    'state':'reconfirmation_required' if stale else 'current',
                    'stale_ids':stale,'confirmation_token':token}}
            if stale_references(policy,state):raise PolicyError(409,'reference_reconfirmation_required')
            approved=tuple(item for item in community.snapshot.items if fingerprint(scope,item) in community.approvals) if community.snapshot else ()
            current_evidence=evidence_current(policy,state,community)
            evidence_by_digest={e['fingerprint']:PathEvidence.from_dict(e['paths']) for e in state['evidence']} if current_evidence else {}
            evidence={item.key:evidence_by_digest[fingerprint(scope,item)] for item in approved if fingerprint(scope,item) in evidence_by_digest}
            if options.get('view')=='field' and options['field_id'] not in {f['id'] for f in state['fields']}:raise PolicyError(404,'not_found')
            result=project(state['fields'],approved,evidence,actor,**options)
            return common|result|{'community_revision':community.revision,'snapshot_available':community.snapshot is not None,
                    'snapshot_observed_at':community.snapshot.observed_at if community.snapshot else None,
                    'evidence_current':current_evidence,'evidence_observed_at':state['observed_at'] if current_evidence else None}
        return self._authenticated_transaction(assertion,operation,write=action in {'save','reconfirm'},with_connection=True)

    def publish_prepared(self,credential,scope,observations,*,expected_revision,community_revision,observed_at):
        """Trusted server integration, NEVER an HTTP ingestion/claims endpoint.

        Caller must independently acquire and approve these paths for this exact
        responsibility reader audience. Uses a REAL configured credential and
        separate publisher grant; cannot authenticate by supplying actor IDs.
        No provider I/O, environment lookup or automatic sharing is performed.
        """
        if type(credential) is not Credential or type(scope) is not Scope:raise ValueError('explicit_credential_and_scope_required')
        prepared=normalize_evidence(observations)
        when=utc_time(observed_at)
        assertion=self._verifier.verify(self._provider_key,credential)
        def publish(policy,actor,db):
            state,community=self._state(policy,actor,db,scope,'publish')
            policy._match(expected_revision,state['revision']);policy._match(community_revision,community.revision);writable(state)
            now=self._clock()
            if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:raise ValueError('invalid_clock')
            if community.snapshot is None or when<utc_time(community.snapshot.observed_at) or when>now.astimezone(timezone.utc):raise PolicyError(409,'invalid_observation_time')
            if state['observed_at'] is not None and when<utc_time(state['observed_at']):raise PolicyError(409,'stale_observation')
            available={fingerprint(scope,item) for item in community.snapshot.items if item.kind=='pull' and item.state=='open'}
            if any(item['fingerprint'] not in available&community.approvals for item in prepared):raise PolicyError(400,'unapproved_evidence_target')
            self._write(db,scope,'evidence=?,community_revision=?,observed_at=?',
                        (evidence_text(scope,prepared,community_revision,observed_at,capture_publisher(policy,actor,state)),community_revision,observed_at),state['revision'])
            self._audit(policy,actor,scope,state['revision']+1)
            return {'scope':scope.as_dict(),'revision':state['revision']+1,'count':len(prepared),'workflow_enabled':False}
        return self._authenticated_transaction(assertion,publish,write=True,with_connection=True)

    @staticmethod
    def _write(db,scope,set_sql,values,expected):
        # set_sql is fixed trusted code; all external values are bound.
        n=db.execute(f'UPDATE {_TABLE} SET {set_sql},revision=revision+1 WHERE workspace_id=? AND connection_id=? AND repository=? AND revision=?',
                     (*values,scope.workspace_id,scope.connection_id,scope.repository,expected)).rowcount
        if n!=1:raise PolicyError(412,'stale_revision')

    @staticmethod
    def _audit(policy,actor,scope,revision):
        audit(policy,actor,scope,revision)

    @staticmethod
    def _route(environ):
        path,query=environ.get('PATH_INFO',''),environ.get('QUERY_STRING','')
        if type(path) is not str or len(path)>1000 or type(query) is not str or len(query)>400:raise PolicyError(400,'invalid_request')
        p=path.split('/');verb=environ.get('REQUEST_METHOD')
        if len(p)!=9 or p[:3]!=['','api','workspaces'] or p[4]!='responsibility' or any(not _ID.fullmatch(x) for x in p[3:]):raise PolicyError(404,'not_found')
        scope=Scope(p[3],p[5],p[6]+'/'+p[7])
        if p[8]=='config' and verb in {'GET','PUT'} and not query:return scope,'config' if verb=='GET' else 'save',{}
        if p[8]=='capabilities' and verb=='GET' and not query:return scope,'capabilities',{}
        if p[8]=='reconfirm' and verb=='POST' and not query:return scope,'reconfirm',{}
        if p[8]!='candidates' or verb!='GET':raise PolicyError(404,'not_found')
        try:
            options=_object(parse_qsl(query,keep_blank_values=True,strict_parsing=True,max_num_fields=4))
            if set(options)-{'view','field_id','offset','limit'}:raise ValueError()
            if options.get('view','mine') not in {'mine','all','field','unassigned'}:raise ValueError()
            if ('field_id' in options and not FIELD_ID.fullmatch(options['field_id'])) or (options.get('view')=='field')!=bool(options.get('field_id')):raise ValueError()
            for k in ('offset','limit'):
                if k in options:
                    if not re.fullmatch(r'[0-9]{1,6}',options[k]):raise ValueError()
                    options[k]=int(options[k])
            if not 0<=options.get('offset',0)<=100000 or not 1<=options.get('limit',50)<=100:raise ValueError()
            return scope,'candidates',options
        except ValueError:raise PolicyError(400,'invalid_request') from None

    @staticmethod
    def _config_body(environ,*,reconfirm=False):
        if environ.get('CONTENT_TYPE','').lower() not in {'application/json','application/json; charset=utf-8'}:raise PolicyError(415,'json_required')
        length=environ.get('CONTENT_LENGTH','')
        if type(length) is not str or not re.fullmatch(r'[0-9]{1,6}',length):raise PolicyError(411,'length_required')
        if int(length)>65536:raise PolicyError(413,'body_too_large')
        try:
            raw=environ['wsgi.input'].read(int(length))
            if type(raw) is not bytes or len(raw)!=int(length):raise ValueError()
            payload=json.loads(raw.decode('utf-8'),object_pairs_hook=_object,parse_constant=lambda _:(_ for _ in ()).throw(ValueError()))
            if type(payload) is not dict or set(payload)!=({'fields','confirmation_token'} if reconfirm else {'fields'}):raise ValueError()
            config=fields(payload['fields'])
            if reconfirm:
                token=payload['confirmation_token']
                if type(token) is not str or re.fullmatch(r'[0-9a-f]{64}',token) is None:raise ValueError()
                return config,token
            return config
        except (ValueError,TypeError,KeyError,UnicodeError,RecursionError):raise PolicyError(400,'invalid_configuration') from None
