"""Cookie/CSRF and SQLite lifecycle integration. Network replaced at GET only."""
from concurrent.futures import ThreadPoolExecutor
from dataclasses import replace
from datetime import timedelta
import io
import json
from pathlib import Path
import sqlite3
import tempfile
import threading
import unittest
from unittest.mock import patch

from packages.application_host import ApplicationHost, HostConfig
from packages.application_host.tests import fixtures as host
from packages.community_api.records import read, replace_policy
from packages.community_board import Scope
from packages.community_refresh import CommunityRefreshConfiguration, RefreshCleanupError, initialize_refresh
from packages.community_sync import BoundCredential
from packages.session_api.application import _SESSION, _CSRF
from packages.session_api.tests.test_application import cookie_header
from packages.workspaces.tests.identity_fixture import simulate_identity, bind_core_grants_for_test
from packages.community_refresh.tests.fixtures import setup

BASE = '/api/workspaces/alpha/community/source/sample/project/'


class RefreshTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.now, self.tick = host.NOW, 10.
        self.app, self.policy, self.login, self.exchange, self.pages = setup(self.tmp.name, clock=lambda: self.now)
        self.app._modules['community']._refresher._monotonic = lambda: self.tick
        self.session = host.session(self.login, self.exchange)
        self.path = Path(self.tmp.name) / 'policy.sqlite'

    def call(self, action='refresh', *, session=None, body=None, extra=None, revision=1, workspace='alpha', app=None):
        session = session or self.session
        raw = json.dumps(body if body is not None else {}).encode()
        env = {'PATH_INFO': BASE.replace('/alpha/', f'/{workspace}/') + action,
            'REQUEST_METHOD': 'GET' if action in ('items', 'review', 'capabilities') else 'POST',
            'QUERY_STRING': '', 'SCRIPT_NAME': '', 'HTTP_HOST': 'app.example.invalid',
            'wsgi.url_scheme': 'https', 'HTTP_ORIGIN': host.ORIGIN,
            'HTTP_COOKIE': cookie_header({_SESSION: session.token.value, _CSRF: session.csrf.value}),
            'HTTP_X_CSRF_TOKEN': session.csrf.value, 'HTTP_IF_MATCH': f'"{revision}"',
            'CONTENT_TYPE': 'application/json', 'CONTENT_LENGTH': str(len(raw)), 'wsgi.input': io.BytesIO(raw)}
        env.update(extra or {}); result = {}
        def start(status, headers):
            result.update(status=int(status.split()[0]), headers=dict(headers))
        result['body'] = json.loads(b''.join((app or self.app)(env, start)))
        return result

    def sql(self, query, args=()):
        with sqlite3.connect(self.path) as db:
            return db.execute(query, args).fetchall()

    def state(self):
        return self.sql("SELECT config,snapshot,approvals,revision FROM community_connections WHERE workspace_id='alpha'")[0]

    def leases(self):
        return self.sql('SELECT actor,state,result_revision FROM community_refresh_leases_v1 ORDER BY rowid')

    def configure(self, **changes):
        config = self.policy._transaction(lambda p, db: read(db, Scope('alpha', 'source', 'sample/project')).policy,
                                         write=False, with_connection=True)
        replace_policy(self.policy, replace(config, **changes), expected_revision=self.state()[3], actor_id='admin')

    def test_private_candidate_and_complete_approval_then_changed_content(self):
        reader = host.session(self.login, self.exchange, 'reader-sub')
        self.configure(reader_ids=('admin', 'reader', 'second'))  # a real grant change clears old content
        self.assertIsNone(self.state()[1]); self.assertEqual(self.call('items', session=reader)['body']['data']['items'], [])
        r = self.call(revision=2); self.assertEqual(r['status'], 200, r)
        self.assertEqual(self.leases(), [('admin', 'complete', 3)])
        self.assertEqual(self.call('items', session=reader)['body']['data']['items'], [])
        self.assertIn(self.call('review', session=reader)['status'], (403,404))
        candidate = self.call('review')['body']['data']['items'][0]
        self.assertFalse(candidate['approved'])
        self.assertEqual(self.call('approve', revision=3, body={'fingerprints':[candidate['fingerprint']]})['status'], 200)
        self.assertEqual(self.call('items', session=reader)['body']['data']['items'][0]['title'], 'Refreshed synthetic issue')
        self.now += timedelta(seconds=1)
        self.assertEqual(self.call(revision=4)['status'], 200)
        self.assertEqual(self.call('items', session=reader)['body']['data']['total'], 1)  # exact unchanged approval retained
        self.pages.rows[0] = dict(self.pages.rows[0], title='Changed candidate', updated_at='2026-01-01T13:00:00Z')
        self.now += timedelta(seconds=1)
        self.assertEqual(self.call(revision=5)['status'], 200)
        self.assertEqual(self.call('items', session=reader)['body']['data']['items'], [])
        self.assertEqual(self.call('review')['body']['data']['items'][0]['title'], 'Changed candidate')
        self.assertEqual(r['headers']['Vary'], 'Cookie, Origin'); self.assertNotIn('WWW-Authenticate', r['headers'])

    def test_disabled_pairing_and_missing_schema_zero_network(self):
        disabled = ApplicationHost(config=replace(self.app._config, community_refresh=False), sessions=self.app._boundary._sessions)
        self.assertIs(self.app._modules['community']._session_boundary,self.app._boundary)
        with self.assertRaises(ValueError): ApplicationHost(config=self.app._config,sessions=self.app._boundary._sessions,community_refresh=self.app._modules['community'])
        self.assertFalse(self.call('capabilities', app=disabled)['body']['data']['capabilities']['refresh'])
        self.assertEqual(self.call(app=disabled)['status'], 503)
        with self.assertRaises(ValueError): ApplicationHost(config=self.app._config, sessions=self.app._boundary._sessions)
        with self.assertRaises(ValueError): ApplicationHost(config=disabled._config, sessions=self.app._boundary._sessions,
            community_refresh=CommunityRefreshConfiguration(self.pages.load, self.pages))
        for value in (1, 'true', None):
            with self.assertRaises(ValueError): replace(self.app._config, community_refresh=value)
        with self.assertRaises(ValueError): HostConfig(host.ORIGIN, (), community_refresh=True)
        with self.assertRaises(ValueError): CommunityRefreshConfiguration(self.pages.load, object())
        self.sql('DROP TABLE community_refresh_leases_v1')
        self.assertEqual(self.call()['status'], 503)
        self.assertEqual(self.pages.loads, []); self.assertEqual(self.pages.calls, [])

    def test_transport_and_body_rejections_before_admission(self):
        variants = ({'HTTP_COOKIE':''}, {'HTTP_X_CSRF_TOKEN':''}, {'HTTP_ORIGIN':'https://other.invalid'},
            {'HTTP_HOST':'other.invalid'}, {'wsgi.url_scheme':'http'}, {'SCRIPT_NAME':'/prefix'},
            {'HTTP_AUTHORIZATION':'Bearer synthetic'}, {'HTTP_SEC_FETCH_SITE':'cross-site'})
        for extra in variants: self.assertEqual(self.call(extra=extra)['status'], 401)
        for body in ({'url':'https://other.invalid'}, {'actor':'admin'}, {'token':'synthetic'}, {'provider':{}}):
            self.assertEqual(self.call(body=body)['status'], 400)
        self.assertEqual(self.call(revision=99)['status'], 412)
        for actor in ('reader', 'second', 'outsider'):
            self.assertIn(self.call(session=host.session(self.login, self.exchange, actor+'-sub'))['status'], (403,404))
        for workspace in ('beta','commons','missing'):
            self.assertIn(self.call(workspace=workspace)['status'], (403,404))
        self.assertEqual(self.leases(), []); self.assertEqual(self.pages.loads, [])

    def test_loader_logout_and_wrong_scope_before_provider(self):
        self.pages.load_hook = lambda: self.login.logout(self.session.token, self.session.csrf)
        self.assertEqual(self.call()['status'], 401)
        self.assertEqual(self.pages.calls, []); self.assertEqual(self.leases(), [('admin','failed',None)])

    def test_bound_credential_mismatch(self):
        self.app._modules['community']._credential_loader = lambda *_: BoundCredential(Scope('beta','source','sample/project'),'synthetic')
        self.assertEqual(self.call()['status'], 502); self.assertEqual(self.pages.calls, [])
        self.assertEqual(self.leases(), [('admin','failed',None)])

    def test_page_revocation_config_cas_identity_and_expiry(self):
        def identity(test):
            with sqlite3.connect(test.path) as db: simulate_identity(db, "UPDATE principals SET subject='changed' WHERE id='admin'")
        mutations = (
            lambda t: t.login.logout(t.session.token,t.session.csrf), lambda t: setattr(t,'now',t.session.expires_at), identity,
            lambda t: t.policy.set_membership('second','alpha','admin',role='admin',state='revoked',expected_revision=1),
            lambda t: t.sql("UPDATE modules SET state='disabled' WHERE workspace_id='alpha' AND key='community'"),
            lambda t: t.policy.set_workspace_state('admin','alpha',state='archived',expected_revision=1),
            lambda t: t.configure(refresher_ids=('second',)), lambda t: t.configure(reader_ids=('admin',)),
            lambda t: t.sql("UPDATE community_connections SET revision=revision+1 WHERE workspace_id='alpha'"))
        for change in mutations:
            with self.subTest(change=change):
                case = RefreshTests(); case.setUp()
                try:
                    after_change=[]
                    def mutate():
                        change(case)
                        after_change.append((case.state(),case.sql('SELECT * FROM audit')))
                    case.pages.page_hook = mutate
                    result = case.call()
                    self.assertIn(result['status'], (401,403,404,409,412), result)
                    self.assertEqual(len(case.pages.calls), 1)
                    self.assertEqual(case.leases(), [('admin','failed',None)])
                    self.assertNotIn('Refreshed synthetic issue', case.state()[1] or '')
                    # Refresh must preserve exactly the independent mutation's outcome.
                    self.assertEqual((case.state(),case.sql('SELECT * FROM audit')),after_change[0])
                finally: case.doCleanups()

    def test_identity_aba_between_pages_is_not_same_user(self):
        def change():
            with sqlite3.connect(self.path) as db:
                simulate_identity(db, "UPDATE principals SET subject='new-sub' WHERE id='admin'")
                simulate_identity(db, "UPDATE principals SET subject='admin-sub' WHERE id='admin'")
        self.pages.page_hook = change
        self.assertEqual(self.call()['status'], 401); self.assertEqual(len(self.pages.calls),1)
        self.assertEqual(self.leases(), [('admin','failed',None)])

    def test_loader_and_page_and_commit_deadlines_roll_back(self):
        for phase in ('loader','page','admit','publish'):
            with self.subTest(phase=phase):
                case=RefreshTests(); case.setUp()
                try:
                    before=case.state(); audit=case.sql('SELECT count(*) FROM audit')
                    if phase == 'loader': case.pages.load_hook=lambda:setattr(case,'tick',case.tick+30)
                    if phase == 'page': case.pages.page_hook=lambda:setattr(case,'tick',case.tick+30)
                    original=case.policy._persist
                    def persist(db,policy,**kw):
                        original(db,policy,**kw)
                        if phase == 'admit' or phase == 'publish' and db.execute("SELECT 1 FROM community_refresh_leases_v1 WHERE state='complete'").fetchone():
                            case.tick+=30
                    with patch.object(case.policy,'_persist',side_effect=persist): result=case.call()
                    self.assertEqual(result['status'],504,result); self.assertEqual(case.state(),before)
                    self.assertEqual(case.sql('SELECT count(*) FROM audit'),audit)
                    self.assertEqual(case.leases(), [] if phase=='admit' else [('admin','failed',None)])
                    if phase in ('loader','admit'): self.assertEqual(case.pages.calls,[])
                finally: case.doCleanups()

    def test_commit_session_expiry_rolls_back_snapshot_audit_completion(self):
        before=self.state(); audit=self.sql('SELECT count(*) FROM audit'); original=self.policy._persist
        def persist(db,policy,**kw):
            original(db,policy,**kw)
            if db.execute("SELECT 1 FROM community_refresh_leases_v1 WHERE state='complete'").fetchone(): self.now=self.session.expires_at
        with patch.object(self.policy,'_persist',side_effect=persist): self.assertEqual(self.call()['status'],401)
        self.assertEqual(self.state(),before); self.assertEqual(self.sql('SELECT count(*) FROM audit'),audit)
        self.assertEqual(self.leases(), [('admin','failed',None)])

    def test_uncertain_cleanup_never_ages_out(self):
        def fail(): self.tick+=31; raise RefreshCleanupError()
        self.pages.page_hook=fail; before=self.state()
        self.assertEqual(self.call()['status'],504); self.assertEqual(self.state(),before)
        self.assertEqual(self.leases(), [('admin','active',None)])
        self.tick+=100; self.now+=timedelta(seconds=100)
        self.assertEqual(self.call()['status'],429); self.assertEqual(len(self.pages.calls),1)

    def test_schema_extensions_and_downgrade_fail_closed(self):
        for statement in ('CREATE TRIGGER unknown AFTER INSERT ON community_refresh_leases_v1 BEGIN SELECT 1; END',
                          'CREATE INDEX unknown ON community_refresh_leases_v1(actor)',
                          'CREATE TRIGGER unknown AFTER UPDATE ON community_connections BEGIN SELECT 1; END'):
            self.sql(statement); self.assertEqual(self.call()['status'],503)
            self.sql('DROP '+('INDEX' if ' INDEX ' in statement else 'TRIGGER')+' unknown')
        self.sql('DROP TABLE community_refresh_leases_v1'); self.sql('CREATE TABLE community_refresh_leases_v1(id TEXT)')
        with self.assertRaises(Exception): initialize_refresh(self.policy)
        self.assertEqual(self.call()['status'],503); self.assertEqual(self.pages.loads,[])

    def test_persist_side_effects_rejected_after_admission_and_completion(self):
        # Triggers on core audit/metadata are not rejected as lease-table schema.
        # The operations actually run, and post-persist readback must roll back.
        cases=(('admit',"UPDATE community_refresh_leases_v1 SET actor='second' WHERE state='active'"),
               ('admit',"UPDATE community_connections SET snapshot=NULL,approvals='{\"version\":2,\"fingerprints\":[],\"provenance\":null}' WHERE workspace_id='alpha'"),
               ('publish',"UPDATE community_refresh_leases_v1 SET state='failed',result_revision=NULL WHERE state='complete'"),
               ('publish',"UPDATE community_connections SET revision=revision+1 WHERE workspace_id='alpha'"))
        for phase,statement in cases:
            with self.subTest(phase=phase,statement=statement):
                case=RefreshTests(); case.setUp()
                try:
                    before=case.state(); audit=case.sql('SELECT count(*) FROM audit')
                    if phase=='admit':
                        case.sql("CREATE TRIGGER interfere AFTER UPDATE ON metadata WHEN EXISTS (SELECT 1 FROM community_refresh_leases_v1 WHERE state='active') BEGIN "+statement+'; END')
                    else:
                        case.sql('CREATE TRIGGER interfere AFTER INSERT ON audit BEGIN '+statement+'; END')
                    result=case.call(); self.assertEqual(result['status'],503,result)
                    self.assertEqual(case.state(),before); self.assertEqual(case.sql('SELECT count(*) FROM audit'),audit)
                    self.assertEqual(case.leases(),[] if phase=='admit' else [('admin','failed',None)])
                    if phase=='admit': self.assertEqual(case.pages.loads,[])
                finally: case.doCleanups()

    def test_invalid_or_overbound_provider_records_no_publication(self):
        for rows in ([{'number':1}], [{'number':n,'title':'x','state':'open','updated_at':'2026-01-01T12:00:00Z'} for n in range(1,102)]):
            self.pages.rows=rows; before=self.state()
            self.assertEqual(self.call()['status'],502); self.assertEqual(self.state(),before)
        self.assertTrue(all(row[1]=='failed' for row in self.leases()))

    def test_concurrent_approval_wins_refresh_cas(self):
        candidate=self.call('review')['body']['data']['items'][0]
        results=[]
        self.pages.page_hook=lambda:results.append(self.call('approve',revision=1,body={'fingerprints':[candidate['fingerprint']]})['status'])
        self.assertEqual(self.call()['status'],412); self.assertEqual(results,[200])
        self.assertNotIn('Refreshed synthetic issue',self.state()[1]); self.assertEqual(self.state()[3],2)
        self.assertEqual(self.leases(),[('admin','failed',None)])

    def test_stale_approval_is_dropped_not_a_refresh_admission_failure(self):
        self.sql("UPDATE memberships SET revision=revision+1 WHERE workspace_id='alpha' AND principal_id='admin'")
        self.assertEqual(self.call('items')['body']['data']['items'],[])
        self.assertEqual(self.call()['status'],200)
        self.assertEqual(self.call('items')['body']['data']['items'],[])

    def test_cross_table_trigger_cannot_release_another_active_worker(self):
        for phase in ('admit','publish','fail'):
            with self.subTest(phase=phase):
                case=RefreshTests();case.setUp()
                try:
                    now=case.now.timestamp()
                    case.sql("INSERT INTO community_refresh_leases_v1 VALUES ('other','second','beta','source','sample/project',1,?, ?,?,'active',NULL)",('0'*64,now,now+30))
                    before=case.state();audit=case.sql('SELECT count(*) FROM audit')
                    if phase=='publish':
                        trigger="CREATE TRIGGER interfere AFTER INSERT ON audit BEGIN DELETE FROM community_refresh_leases_v1 WHERE id='other'; END"
                    else:
                        state='active' if phase=='admit' else 'failed'
                        trigger="CREATE TRIGGER interfere AFTER UPDATE ON metadata WHEN EXISTS (SELECT 1 FROM community_refresh_leases_v1 WHERE id!='other' AND state='"+state+"') BEGIN DELETE FROM community_refresh_leases_v1 WHERE id='other'; END"
                    case.sql(trigger)
                    if phase=='fail':case.pages.rows=[{}]
                    result=case.call();self.assertEqual(result['status'],503,result)
                    self.assertEqual(case.state(),before);self.assertEqual(case.sql('SELECT count(*) FROM audit'),audit)
                    self.assertEqual(case.sql("SELECT state FROM community_refresh_leases_v1 WHERE id='other'"),[('active',)])
                    if phase=='admit':self.assertEqual(case.pages.loads,[]);self.assertEqual(len(case.leases()),1)
                    elif phase=='publish':self.assertEqual(case.leases()[1],('admin','failed',None))
                    else:self.assertEqual(case.leases()[1],('admin','active',None))
                finally:case.doCleanups()

    def test_total_snapshot_byte_bound_is_not_a_partial_publication(self):
        from urllib.parse import parse_qs,urlsplit
        def page(path,credential,connection,*,remaining):
            self.pages.calls.append((path,credential.scope,remaining))
            number=int(parse_qs(urlsplit(path).query)['page'][0])
            return [{'number':(number-1)*100+n,'title':'x'*1000,'state':'open',
                     'updated_at':'2026-01-01T12:00:00Z'} for n in range(1,101)]
        self.pages.get=page;before=self.state();audit=self.sql('SELECT count(*) FROM audit')
        self.assertEqual(self.call()['status'],502);self.assertEqual(self.state(),before)
        self.assertEqual(self.sql('SELECT count(*) FROM audit'),audit)
        self.assertEqual(len(self.pages.calls),20);self.assertEqual(self.leases(),[('admin','failed',None)])

    def test_revision_exhaustion_no_admission_or_secret(self):
        maximum=9007199254740991
        self.sql("UPDATE community_connections SET revision=? WHERE workspace_id='alpha'",(maximum,))
        self.assertEqual(self.call(revision=maximum)['status'],409)
        self.assertEqual(self.pages.loads,[]);self.assertEqual(self.leases(),[])

    def test_same_scope_overlap_and_late_worker_fence(self):
        entered, release=threading.Event(),threading.Event(); self.addCleanup(release.set)
        self.pages.page_hook=lambda:(entered.set(),release.wait(5))
        with ThreadPoolExecutor(max_workers=1) as pool:
            first=pool.submit(self.call); self.assertTrue(entered.wait(2))
            self.assertEqual(self.call()['status'],429); self.assertEqual(len(self.pages.loads),1)
            self.sql("UPDATE community_refresh_leases_v1 SET state='failed' WHERE state='active'")
            release.set(); self.assertIn(first.result()['status'],(412,503))
        self.assertNotIn('Refreshed synthetic issue',self.state()[1])

    def test_independent_hosts_share_global_actor_and_scope_admission(self):
        with sqlite3.connect(self.path) as db:
            for actor in ('admin','second','outsider'):
                for workspace in ('alpha','beta','commons'):
                    db.execute("INSERT OR REPLACE INTO memberships VALUES (?,?,'admin','active',1)",(workspace,actor))
                bind_core_grants_for_test(db,(actor,))
        for workspace in ('alpha','beta','commons'):
            scope=Scope(workspace,'source','sample/project')
            config=self.policy._transaction(lambda p,db:read(db,scope).policy,write=False,with_connection=True)
            replace_policy(self.policy,replace(config,refresher_ids=('admin','second','outsider')),expected_revision=1,actor_id='admin')
        other=ApplicationHost(config=self.app._config,sessions=self.app._boundary._sessions,clock=lambda:self.now,
                             community_refresh=CommunityRefreshConfiguration(self.pages.load,self.pages))
        second=host.session(self.login,self.exchange,'second-sub'); third=host.session(self.login,self.exchange,'outsider-sub')
        entered,release,first_page=threading.Barrier(3),threading.Event(),threading.Event(); self.addCleanup(release.set)
        self.pages.page_hook=lambda:(first_page.set(),entered.wait(3),release.wait(5))
        with ThreadPoolExecutor(max_workers=2) as pool:
            one=pool.submit(self.call,revision=2)
            # Actor limit and scope limit are checked with global capacity still available.
            self.assertTrue(first_page.wait(2))
            self.assertEqual(self.call(workspace='beta',revision=2)['status'],429)
            self.assertEqual(self.call(session=second,revision=2,app=other)['status'],429)
            two=pool.submit(self.call,workspace='beta',session=second,revision=2,app=other)
            entered.wait(3)
            self.assertEqual(self.call(workspace='commons',session=third,revision=2)['status'],429)
            self.now+=timedelta(seconds=31)
            self.assertEqual(self.call(workspace='commons',session=third,revision=2)['status'],429)
            release.set(); self.assertEqual(one.result()['status'],504); self.assertEqual(two.result()['status'],504)
        self.assertTrue(all(row[1]=='failed' for row in self.leases()))


if __name__ == '__main__': unittest.main()
