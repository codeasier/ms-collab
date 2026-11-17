"""Real spawned processes share SQLite admission and wait for current writer state."""
from dataclasses import replace
import multiprocessing
from pathlib import Path
import sqlite3
import tempfile
import unittest

from packages.application_host.tests import fixtures as host
from packages.community_api.records import read, replace_policy
from packages.community_board import Scope
from packages.workspaces.tests.identity_fixture import bind_core_grants_for_test
from packages.community_refresh.tests.fixtures import setup, process_contender


class ProcessTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.app,self.policy,self.login,self.exchange,self.pages=setup(self.tmp.name)
        self.session=host.session(self.login,self.exchange)
        self.path=Path(self.tmp.name)/'policy.sqlite'
        self.context=multiprocessing.get_context('spawn');self.children=[]
        self.addCleanup(self.stop)

    def stop(self):
        failed=[]
        for process,release,replies in self.children:release.set()
        for process,release,replies in self.children:
            process.join(10)
            if process.is_alive():
                process.terminate();process.join(3);failed.append('process barrier leaked')
            if process.exitcode != 0:failed.append('process did not exit cleanly')
            replies.close();replies.join_thread()
        self.assertFalse(failed,failed)

    def child(self, *, session=None, workspace='alpha', revision=1, pause=False):
        session=session or self.session;ctx=self.context
        replies,start,entered,release,waiting,tick=ctx.Queue(),ctx.Event(),ctx.Event(),ctx.Event(),ctx.Event(),ctx.Value('d',10.)
        if not pause:release.set()
        child=ctx.Process(target=process_contender,args=(self.tmp.name,session.token.value,session.csrf.value,
            workspace,revision,replies,start,entered,release,waiting,tick))
        child.start();self.children.append((child,release,replies))
        self.assertEqual(replies.get(timeout=8),('ready',))
        return replies,start,entered,release,waiting,tick

    def test_two_process_acquisitions_reserve_global_actor_and_scope_capacity(self):
        with sqlite3.connect(self.path) as db:
            for actor in ('admin','second','outsider'):
                for workspace in ('alpha','beta','commons'):
                    db.execute("INSERT OR REPLACE INTO memberships VALUES (?,?,'admin','active',1)",(workspace,actor))
                bind_core_grants_for_test(db,(actor,))
        for workspace in ('alpha','beta','commons'):
            scope=Scope(workspace,'source','sample/project')
            config=self.policy._transaction(lambda p,db:read(db,scope).policy,write=False,with_connection=True)
            replace_policy(self.policy,replace(config,refresher_ids=('admin','second','outsider')),expected_revision=1,actor_id='admin')
        second=host.session(self.login,self.exchange,'second-sub');third=host.session(self.login,self.exchange,'outsider-sub')
        one=self.child(revision=2,pause=True);one[1].set();self.assertTrue(one[2].wait(5))
        # Two independently opened hosts see the first process's actual lease.
        for session,workspace in ((self.session,'beta'),(second,'alpha')):
            denied=self.child(session=session,workspace=workspace,revision=2);denied[1].set()
            self.assertEqual(denied[0].get(timeout=5)[1:4],(429,0,0))
        two=self.child(session=second,workspace='beta',revision=2,pause=True);two[1].set();self.assertTrue(two[2].wait(5))
        denied=self.child(session=third,workspace='commons',revision=2);denied[1].set()
        self.assertEqual(denied[0].get(timeout=5)[1:4],(429,0,0))
        one[3].set();two[3].set()
        self.assertEqual(one[0].get(timeout=5)[1],200);self.assertEqual(two[0].get(timeout=5)[1],200)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute('SELECT state FROM community_refresh_leases_v1 ORDER BY rowid').fetchall(),[('complete',),('complete',)])
        stale=self.child(revision=2);stale[1].set();self.assertEqual(stale[0].get(timeout=5)[1:4],(412,0,0))

    def test_waiting_writer_lock_rechecks_revision_before_secret(self):
        worker=self.child()
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            db.execute("UPDATE community_connections SET revision=revision+1 WHERE workspace_id='alpha'")
            worker[1].set();self.assertTrue(worker[4].wait(5))
            self.assertFalse(worker[2].is_set())
            db.commit()
        self.assertEqual(worker[0].get(timeout=5)[1:4],(412,0,0))
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM community_refresh_leases_v1').fetchone()[0],0)

    def test_waiting_writer_lock_counts_toward_total_deadline(self):
        worker=self.child()
        with sqlite3.connect(self.path) as db:
            db.execute('BEGIN IMMEDIATE')
            worker[1].set();self.assertTrue(worker[4].wait(5))
            with worker[5].get_lock():worker[5].value+=31
            db.commit()
        self.assertEqual(worker[0].get(timeout=5)[1:4],(504,0,0))
        with sqlite3.connect(self.path) as db:self.assertEqual(db.execute('SELECT count(*) FROM community_refresh_leases_v1').fetchone()[0],0)


if __name__=='__main__':unittest.main()
