"""Synthetic unified host setup only. Never imported by the application factory."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3

from packages.application_host import ApplicationHost, HostConfig
from packages.artifacts import Scope as ArtifactScope, Snapshot as ArtifactSnapshot
from packages.artifact_sync import Connection as ArtifactConnection, IndexPolicy, initialize_indexes
from packages.artifact_sync.records import snapshot_text
from packages.assistant_history import initialize as initialize_history, create_conversation, append_events, DisplayEvent
from packages.community_api import ConnectionPolicy, initialize as initialize_community
from packages.community_api.records import encode, snapshot_data, fingerprint
from packages.community_board.model import Scope, Snapshot, BoardItem
from packages.community_sync import Connection
from packages.identity import AuthenticationError, VerifiedIdentity
from packages.login_state import LoginBinding, LoginCoordinator, SQLiteLoginStore, s256_challenge
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipeline_api.tests.test_application import run
from packages.pipelines import PipelineScope
from packages.research_assets import AssetScope, CatalogBinding, Entity, SourceSpec, provision_catalog
from packages.responsibility_api import ResponsibilityPolicy, initialize as initialize_responsibility
from packages.session_api import SessionPolicyStore
from packages.workspace_admin_api import ModuleTemplate, initialize_administration
from packages.workspace_invitations import initialize_invitations
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, Resource, SQLiteWorkspaceService

NOW = datetime(2026, 1, 2, tzinfo=timezone.utc)
ORIGIN = 'https://app.example.invalid'
ISSUER = 'https://identity.example.invalid'
MODULES = ('community','pipelines','artifacts','research-assets','responsibility','assistant')
READS = (
 '/api/workspaces/alpha/community/source/sample/project/items',
 '/api/workspaces/alpha/pipelines/example-ci/build/runs',
 '/api/workspaces/alpha/artifacts/source/bucket/quarters',
 '/api/workspaces/alpha/research-assets/catalog/types',
 '/api/workspaces/alpha/responsibility/source/sample/project/config',
 '/api/workspaces/alpha/assistant/conversations/one/events',
)


class SyntheticExchange:
    def __init__(self,binding,clock): self.binding,self.clock,self.codes=binding,clock,{}
    def authorize(self,challenge,subject='admin-sub'):
        import secrets
        code='synthetic-'+secrets.token_urlsafe(24);self.codes[code]=(challenge,subject);return code
    def exchange_bound_code(self,*,code,verifier,callback_uri):
        record=self.codes.pop(code.value,None)
        if record is None or record[0]!=s256_challenge(verifier) or callback_uri!=self.binding.callback_uri:
            raise AuthenticationError()
        return VerifiedIdentity(self.binding.issuer,record[1],self.binding.audience,self.clock(),self.clock()+timedelta(minutes=5))


def initialize(path):
    people=('admin','reader','second','outsider'); spaces=('alpha','beta','commons')
    scopes=tuple(PipelineScope(w,'example-ci','build') for w in spaces)
    policy_keys=tuple('research_assets' if k=='research-assets' else k for k in MODULES)
    store=SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p,ISSUER,p+'-sub') for p in people),
        workspaces=tuple(Workspace(w,w,'shared' if w=='commons' else 'product') for w in spaces),
        memberships=(Membership('alpha','admin','admin'),Membership('alpha','reader','viewer'),
                     Membership('alpha','second','admin'),Membership('beta','second','admin'),Membership('commons','second','admin')),
        modules=tuple(ModuleInstance(w,k,('read','write')) for w in spaces for k in policy_keys),
        resources=tuple(Resource(s.workspace_id,'pipelines',resource_id(s),encode_snapshots(s,[run(s)])) for s in scopes),
        operators=frozenset({'admin'}))
    initialize_administration(store)
    initialize_invitations(store)
    community_scopes=tuple(Scope(w,'source','sample/project') for w in spaces)
    initialize_community(store,tuple(ConnectionPolicy(Connection(s),'synthetic-reference',('admin','reader'),('admin',),('admin',)) for s in community_scopes))
    initialize_responsibility(store,tuple(ResponsibilityPolicy(s,('admin','reader'),('admin',),('admin',)) for s in community_scopes))
    artifacts=tuple(ArtifactScope(w,'source','bucket') for w in spaces)
    initialize_indexes(store,tuple(IndexPolicy(ArtifactConnection(s,'synthetic-bucket','eu-west-101'),
                             'synthetic-reference',('admin','reader'),('admin',),('admin',)) for s in artifacts))
    for w in spaces:
        provision_catalog(store,CatalogBinding(AssetScope(w,'catalog'),'synthetic-namespace',
            (Entity('tool','Synthetic tool'),),(SourceSpec('source',('tool',),('api',)),)))
    initialize_history(store)
    create_conversation(store,owner='admin',workspace='alpha',conversation='one',title='Synthetic saved history',clock=lambda:NOW)
    append_events(store,owner='admin',workspace='alpha',conversation='one',events=(DisplayEvent('assistant_message','Synthetic answer'),),expected_revision=1,clock=lambda:NOW)
    # Fixture provisioning only, not an ingestion endpoint or production migration.
    with sqlite3.connect(path) as db:
        for s in community_scopes:
            snapshot=Snapshot(s,'2026-01-01T00:00:00Z',(BoardItem('issue',1,'Approved synthetic issue','open','2026-01-01T00:00:00Z'),BoardItem('issue',2,'Private synthetic issue','open','2026-01-01T00:00:00Z')))
            db.execute('UPDATE community_connections SET snapshot=?,approvals=? WHERE workspace_id=?',
                       (encode(snapshot_data(snapshot)),encode([fingerprint(s,snapshot.items[0])]),s.workspace_id))
        for s in artifacts:
            db.execute('UPDATE artifact_indexes_v1 SET published=?,published_revision=1 WHERE workspace=?',
                       (snapshot_text(ArtifactSnapshot(s,'2026-01-01T00:00:00Z',())),s.workspace_id))
    return store


def setup(directory,*,origin=ORIGIN,clock=lambda:NOW,enabled=True,modules=MODULES,authorization_endpoint=None):
    policy=initialize(Path(directory)/'policy.sqlite')
    binding=LoginBinding(ISSUER,'synthetic-host-client',origin+'/auth/callback')
    login=SQLiteLoginStore.initialize(Path(directory)/'login.sqlite',binding=binding,clock=clock)
    sessions=SessionPolicyStore(policy=policy,login=login)
    exchange=SyntheticExchange(binding,clock)
    args=dict(exchange=exchange,authorization_endpoint=authorization_endpoint or ISSUER+'/authorize',authorization_scopes=('synthetic_user',)) if enabled else {}
    app=ApplicationHost(config=HostConfig(origin,modules,tuple(ModuleTemplate('research_assets' if k=='research-assets' else k,('read','write')) for k in modules)),sessions=sessions,clock=clock,**args)
    return app,policy,login,exchange


def session(login,exchange,subject='admin-sub'):
    c=LoginCoordinator(login,exchange=exchange);attempt=c.begin()
    return c.complete(parameters=(('state',attempt.state.value),('code',exchange.authorize(attempt.code_challenge,subject))),
                      browser_binding=attempt.browser_binding,callback_uri=login.binding.callback_uri)
