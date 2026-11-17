"""Synthetic GitCode transport only; real host, session, policy and JSON provider."""
from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures as host
from packages.community_refresh import CommunityRefreshConfiguration, initialize_refresh
from packages.community_sync import BoundCredential


class Pages:
    def __init__(self):
        self.loads, self.calls = [], []
        self.load_hook = self.page_hook = lambda: None
        self.rows = [{'number': 1, 'title': 'Refreshed synthetic issue', 'state': 'open',
                      'updated_at': '2026-01-01T12:00:00Z', 'labels': []}]
    def load(self, scope, reference):
        self.loads.append((scope, reference)); self.load_hook()
        return BoundCredential(scope, 'synthetic-not-a-real-token')
    def get(self, path, credential, connection, *, remaining):
        self.calls.append((path, credential.scope, remaining)); self.page_hook()
        return list(self.rows) if '/issues?' in path else []


def setup(directory, *, clock=lambda: host.NOW, enabled=True, schema=True):
    base, policy, login, exchange = host.setup(directory, clock=clock, modules=('community',))
    if schema:
        initialize_refresh(policy)
    pages = Pages()
    app = ApplicationHost(config=HostConfig(host.ORIGIN, ('community',), community_refresh=enabled),
        sessions=base._boundary._sessions, clock=clock,
        community_refresh=CommunityRefreshConfiguration(pages.load, pages) if enabled else None)
    return app, policy, login, exchange, pages


class BrowserPages(Pages):
    def __init__(self):
        super().__init__()
        self.rows = [{'number': n, 'title': '<img src=x onerror=alert(1)>' if n == 101 else f'Refreshed synthetic issue {n}',
                      'state': 'open', 'updated_at': '2026-01-02T00:00:00Z',
                      'labels': [{'name':'<b>plain label</b>'}]} for n in range(101,0,-1)]
    def get(self, path, credential, connection, *, remaining):
        from urllib.parse import parse_qs, urlsplit
        self.calls.append((path, credential.scope, remaining)); self.page_hook()
        page = int(parse_qs(urlsplit(path).query)['page'][0])
        start = (page - 1) * connection.page_size
        return self.rows[start:start+connection.page_size] if '/issues?' in path else []


def process_contender(directory, token, csrf, workspace, revision, replies, start, entered, release, waiting, tick):
    """Spawned independent host over the SAME policy/login files. Synthetic GET only."""
    import io
    import json
    from pathlib import Path
    from packages.login_state import LoginBinding, SQLiteLoginStore
    from packages.session_api import SessionPolicyStore
    from packages.workspaces import SQLiteWorkspaceService
    from packages.session_api.application import _SESSION, _CSRF
    from packages.session_api.tests.test_application import cookie_header
    try:
        policy = SQLiteWorkspaceService(Path(directory) / 'policy.sqlite')
        binding = LoginBinding(host.ISSUER,'synthetic-host-client',host.ORIGIN+'/auth/callback')
        login = SQLiteLoginStore(Path(directory) / 'login.sqlite',binding=binding,clock=lambda:host.NOW)
        sessions = SessionPolicyStore(policy=policy,login=login)
        pages = Pages()
        def page():
            entered.set()
            if not release.wait(20): raise RuntimeError('synthetic process barrier timeout')
        pages.page_hook = page
        app = ApplicationHost(config=HostConfig(host.ORIGIN,('community',),community_refresh=True),
            sessions=sessions,clock=lambda:host.NOW,community_refresh=CommunityRefreshConfiguration(pages.load,pages))
        app._modules['community']._refresher._monotonic=lambda:tick.value
        # Instrument only SQL arrival, not authentication/admission or execution.
        original=sessions._connect
        def connect():
            db=original()
            db.set_trace_callback(lambda sql:waiting.set() if sql=='BEGIN IMMEDIATE' else None)
            return db
        sessions._connect=connect
        replies.put(('ready',))
        if not start.wait(8): raise RuntimeError('synthetic start barrier timeout')
        env={'PATH_INFO':f'/api/workspaces/{workspace}/community/source/sample/project/refresh',
            'REQUEST_METHOD':'POST','QUERY_STRING':'','SCRIPT_NAME':'','HTTP_HOST':'app.example.invalid',
            'wsgi.url_scheme':'https','HTTP_ORIGIN':host.ORIGIN,
            'HTTP_COOKIE':cookie_header({_SESSION:token,_CSRF:csrf}),'HTTP_X_CSRF_TOKEN':csrf,
            'HTTP_IF_MATCH':f'"{revision}"','CONTENT_TYPE':'application/json','CONTENT_LENGTH':'2','wsgi.input':io.BytesIO(b'{}')}
        result={}
        def respond(status,headers):result['status']=int(status.split()[0])
        body=json.loads(b''.join(app(env,respond)))
        replies.put(('result',result['status'],len(pages.loads),len(pages.calls),body))
    except BaseException as error:
        replies.put(('fixture_failed',type(error).__name__))
