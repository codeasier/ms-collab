"""Synthetic foundation fixtures. Never imported by product/runtime code."""
from datetime import datetime, timedelta, timezone
from hashlib import sha256
from packages.directory_cursors import Projection, ReceiptOnly, ReceiptStore
from packages.identity import AuthenticationError, Credential, Provider, VerifiedIdentity
from packages.workspace_api import WorkspaceApplication
from packages.workspaces import Membership, ModuleInstance, Principal, SQLiteWorkspaceService, Workspace

NOW = datetime(2026, 1, 1, tzinfo=timezone.utc)
ISSUER = 'https://identity.example.invalid'


class Clock:
    def __init__(self): self.now = NOW
    def __call__(self): return self.now


class SyntheticVerifier:
    def verify(self, credential):
        if type(credential) is not Credential or credential.value not in ('admin', 'other', 'viewer', 'outsider'):
            raise AuthenticationError()
        return VerifiedIdentity(ISSUER, credential.value + '-sub', 'synthetic', NOW, NOW + timedelta(hours=1))


def initialize(path):
    return SQLiteWorkspaceService.initialize(path,
        principals=tuple(Principal(p, ISSUER, p + '-sub') for p in ('admin','other','viewer','outsider')),
        workspaces=tuple(Workspace(w, w) for w in ('alpha','beta','commons')),
        memberships=tuple(Membership(w,p,'viewer' if p == 'viewer' else 'admin')
                          for w in ('alpha','beta','commons') for p in ('admin','other','viewer')),
        modules=tuple(ModuleInstance(w,m,('read','write')) for w in ('alpha','beta','commons') for m in ('pipelines','artifacts')),
        operators=frozenset({'admin'}))


def projection(policy, actor, db):
    # No production Pipeline/Artifact authority/provenance projection is implied.
    return Projection(sha256(b'synthetic authority').digest(), sha256(b'synthetic visible set').digest(), 'synthetic')


def app_for(policy, clock):
    return WorkspaceApplication(store=policy, providers=(Provider('synthetic',ISSUER,'synthetic',SyntheticVerifier(),timedelta(hours=1)),),
        provider_key='synthetic', clock=clock)


def plan_for(policy, clock, *, workspace='alpha', action='pipeline.scopes.read', project=projection, allow_refresh=False):
    return ReceiptOnly(ReceiptStore(policy, clock=clock), workspace, action, project, allow_refresh)


def assertion(app, *, actor='admin', extra=None):
    env = {'HTTP_AUTHORIZATION':'Bearer '+actor,'PATH_INFO':'/api/workspaces/alpha/pipelines/scopes'}
    if extra: env.update(extra)
    return app._verify_request(env)


def issue_at(anchor=('conn','pipeline')):
    def operation(policy, actor, db, receipts):
        receipts.arm()
        receipts.view()
        return receipts.issue(anchor)
    return operation


def resume(token):
    def operation(policy, actor, db, receipts):
        receipts.arm()
        receipts.view()
        return receipts.resume(token)
    return operation


def invoke(app, plan, operation=None, *, actor='admin'):
    return app._authenticated_transaction(assertion(app, actor=actor), operation or issue_at(), with_connection=True, receipt_only=plan)


def process_issue(path, anchor, ready, release, queue):
    try:
        policy = SQLiteWorkspaceService(path, timeout=5)
        clock = Clock(); app = app_for(policy,clock); plan=plan_for(policy,clock)
        ready.set()
        if not release.wait(10): raise RuntimeError('synthetic barrier timeout')
        queue.put(('ok', invoke(app,plan,issue_at(anchor))))
    except Exception as error:
        queue.put(('error', getattr(error,'code',type(error).__name__)))


def process_resume(path, token, queue):
    try:
        policy=SQLiteWorkspaceService(path); clock=Clock()
        queue.put(('ok',invoke(app_for(policy,clock),plan_for(policy,clock),resume(token))))
    except Exception as error:
        queue.put(('error',getattr(error,'code',type(error).__name__)))


def process_waiting_issue(path, ready, release, attempting, queue):
    try:
        policy=SQLiteWorkspaceService(path,timeout=5);clock=Clock()
        app=app_for(policy,clock);plan=plan_for(policy,clock)
        original=policy._connect
        def connect():
            db=original()
            db.set_trace_callback(lambda sql: attempting.set() if sql == 'BEGIN IMMEDIATE' else None)
            return db
        policy._connect=connect
        ready.set()
        if not release.wait(10):raise RuntimeError('synthetic barrier timeout')
        queue.put(('ok',invoke(app,plan)))
    except Exception as error:queue.put(('error',getattr(error,'code',type(error).__name__)))


def process_cookie(path, login_path, token, entered, release, queue, after=None):
    try:
        from packages.login_state import SQLiteLoginStore
        from packages.session_api import SessionPolicyStore, SessionRequest
        from packages.session_api.tests.test_application import BINDING
        policy=SQLiteWorkspaceService(path);clock=Clock()
        login=SQLiteLoginStore(login_path,binding=BINDING,clock=clock)
        sessions=SessionPolicyStore(policy=policy,login=login);plan=plan_for(policy,clock)
        def work(policy,actor,db,receipts):
            receipts.arm();receipts.view();entered.set()
            if not release.wait(10):raise RuntimeError('synthetic barrier timeout')
            return receipts.resume(after) if after else receipts.issue(('conn','pipeline'))
        value=sessions.transaction(SessionRequest(Credential(token)),work,with_connection=True,receipt_only=plan)
        queue.put(('ok',value))
    except Exception as error:queue.put(('error',getattr(error,'code',type(error).__name__)))
