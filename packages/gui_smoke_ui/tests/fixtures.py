"""Synthetic temporary databases and bound-code identity. Never production imports."""
from datetime import datetime, timedelta, timezone
from pathlib import Path

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests.fixtures import SyntheticExchange, session
from packages.gui_smoke import SmokeScope, SmokeConnection, initialize, publish_snapshot
from packages.gui_smoke.tests.test_smoke import task
from packages.gui_smoke_ui import GuiSmokeUI
from packages.login_state import LoginBinding, SQLiteLoginStore
from packages.session_api import SessionPolicyStore
from packages.workspace_admin_api import ModuleTemplate, initialize_administration
from packages.workspace_invitations import initialize_invitations
from packages.workspaces import Principal, Workspace, Membership, ModuleInstance, SQLiteWorkspaceService

NOW = datetime(2026, 1, 3, tzinfo=timezone.utc)
ORIGIN = 'https://app.example.invalid'
ISSUER = 'https://identity.example.invalid'
BASE = '/api/workspaces/alpha/gui-smoke/panel/repo'
XSS = '<img src=x onerror="globalThis.injected=1">'


def tasks(workspace='alpha'):
    rows=[]
    for index in range(33):
        # Newest-first stable IDs make first-page details and pagination deterministic.
        row=task('task-'+str(index+1), created_at=(NOW-timedelta(hours=4,minutes=index)).isoformat(),
                 started_at=(NOW-timedelta(hours=2)).isoformat(), finished_at=(NOW-timedelta(hours=1)).isoformat(),
                 branch=XSS if index == 0 and workspace == 'alpha' else workspace+'-branch')
        if index % 6 == 0:
            row.update(status='failed', summary={'passed':4,'failed':1,'skipped':1,'total':6})
            row['stages'][0].update(status='failed',name='Synthetic GUI assertions')
        elif index % 6 == 1: row.update(status='running',finished_at=None,summary=None,stages=None)
        elif index % 6 == 2: row.update(status='pending',started_at=None,finished_at=None,commit=None,summary=None,stages=None)
        elif index % 6 == 3: row.update(status='cancelled',summary=None,stages=None)
        elif index % 6 == 4: row.update(status='unknown',summary=None,stages=None)
        rows.append(row)
    return tuple(rows)


def setup(directory, *, origin=ORIGIN, clock=lambda:NOW, enabled=True, authorization_endpoint=None):
    people=('alice','bob','writer','admin'); spaces=('alpha','beta','commons','empty','unpublished')
    policy=SQLiteWorkspaceService.initialize(Path(directory)/'policy.sqlite',
        principals=tuple(Principal(p,ISSUER,p+'-sub') for p in people),
        workspaces=tuple(Workspace(w,w,'shared' if w=='commons' else 'product') for w in spaces),
        memberships=tuple(Membership(w,p,'admin' if p=='admin' else 'developer' if p=='writer' else 'viewer') for w in spaces for p in people),
        modules=tuple(ModuleInstance(w,'gui_smoke',('read','write')) for w in spaces))
    initialize_administration(policy); initialize_invitations(policy); initialize(policy)
    connections=tuple(SmokeConnection(SmokeScope(w,'panel','repo'),frozenset({'bob' if w=='commons' else 'alice'}),frozenset({'writer'})) for w in spaces)
    for c in connections:
        if c.scope.workspace_id != 'unpublished':
            publish_snapshot(policy,c,actor='writer',tasks=() if c.scope.workspace_id=='empty' else tasks(c.scope.workspace_id),
                observed_at=(clock()-timedelta(seconds=20)).isoformat(),expected_revision=0,clock=clock)
    binding=LoginBinding(ISSUER,'synthetic-gui-client',origin+'/auth/callback')
    login=SQLiteLoginStore.initialize(Path(directory)/'login.sqlite',binding=binding,clock=clock)
    sessions=SessionPolicyStore(policy=policy,login=login)
    exchange=SyntheticExchange(binding,clock)
    args=dict(exchange=exchange,authorization_endpoint=authorization_endpoint or ISSUER+'/authorize',authorization_scopes=('synthetic_user',)) if enabled else {}
    config=HostConfig(origin,('gui-smoke',),(ModuleTemplate('gui_smoke',('read','write')),),connections)
    host=ApplicationHost(config=config,sessions=sessions,clock=clock,**args)
    return GuiSmokeUI(host=host),policy,login,exchange,connections


def cookies(login,exchange,subject='alice-sub'):
    created=session(login,exchange,subject)
    return '__Host-ms-collab-session='+created.token.value+'; __Host-ms-collab-csrf='+created.csrf.value,created.csrf.value
