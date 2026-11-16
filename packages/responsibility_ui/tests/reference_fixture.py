"""Temporary-database browser controls only; never a production entrypoint."""
import sqlite3
from packages.workspaces.tests.identity_fixture import simulate_identity_with_core_regrant as simulate_identity


def identity_aba(path, target):
    with sqlite3.connect(path) as db:
        subject = db.execute('SELECT subject FROM principals WHERE id=?', (target,)).fetchone()[0]
        simulate_identity(db, 'UPDATE principals SET subject=? WHERE id=?', (subject+'-synthetic-aba', target))
        simulate_identity(db, 'UPDATE principals SET subject=? WHERE id=?', (subject, target))


def membership_change(policy, path, target, state):
    with sqlite3.connect(path) as db:
        role, revision = db.execute('SELECT role,revision FROM memberships WHERE workspace_id=? AND principal_id=?', ('alpha', target)).fetchone()
    if state=='active':
        # Synthetic future restore only, as in community authority regression tests.
        # The real service intentionally refuses reactivation and is not changed.
        with sqlite3.connect(path) as db:
            db.execute("UPDATE memberships SET state='active',revision=revision+1 WHERE workspace_id='alpha' AND principal_id=?", (target,))
    else:
        policy.set_membership('admin', 'alpha', target, role=role, state=state, expected_revision=revision)
