"""Disposable TEST-ONLY simulation of a future directory transition.

The production mutation entry and DB guard are deliberately closed. This helper
briefly bypasses the guard inside a savepoint only to test consumer invalidation.
It is not the future mutation algorithm: it does not implement role revocation,
last-admin/operator safeguards or mutation audit, and must never be imported by
runtime code. Fixtures deliberately keep other grants unchanged to isolate the
principal-generation fence from membership revision changes.
"""
from packages.workspaces.sqlite import _PRINCIPAL_GUARDS


def simulate_identity(connection, statement, values=()):
    if not statement.startswith("UPDATE principals SET "):
        raise ValueError("synthetic principal update only")
    connection.execute("SAVEPOINT synthetic_identity_change")
    try:
        before = dict((row[0], row[1:]) for row in connection.execute(
            "SELECT id,issuer,subject,active,generation FROM principals").fetchall())
        connection.execute("DROP TRIGGER principal_authority_closed")
        result = connection.execute(statement, values).fetchall()
        after = connection.execute("SELECT id,issuer,subject,active FROM principals").fetchall()
        for ident, issuer, subject, active in after:
            if before[ident][:3] != (issuer, subject, active):
                connection.execute("UPDATE metadata SET epoch=epoch+1 WHERE id=1")
                epoch = connection.execute("SELECT epoch FROM metadata WHERE id=1").fetchone()[0]
                connection.execute("UPDATE principals SET generation=?,authority_epoch=? WHERE id=?",
                                   (before[ident][3] + 1, epoch, ident))
        connection.execute(_PRINCIPAL_GUARDS[0])
        connection.execute("RELEASE synthetic_identity_change")
        return result
    except BaseException:
        connection.execute("ROLLBACK TO synthetic_identity_change")
        connection.execute("RELEASE synthetic_identity_change")
        raise


def bind_core_grants_for_test(connection, principal_ids):
    """Explicit synthetic core regrant for tests isolating OTHER consumers.

    This deliberately replaces only membership/operator sidecar pins, including
    synthetic newly inserted core rows. It leaves all module/history/retry pins
    and revision counters untouched, so their own ABA checks remain exercised.
    It is not a supported runtime grant-recovery or identity-transition protocol.
    Core-ownership tests must use simulate_identity without this helper.
    """
    realm = connection.execute('SELECT realm FROM metadata WHERE id=1').fetchone()[0]
    for principal_id in principal_ids:
        issuer, subject, generation = connection.execute(
            'SELECT issuer,subject,generation FROM principals WHERE id=?', (principal_id,)).fetchone()
        for workspace_id, in connection.execute(
                'SELECT workspace_id FROM memberships WHERE principal_id=?', (principal_id,)).fetchall():
            connection.execute(
                'INSERT INTO membership_authority VALUES (?,?,?,?,?,?) '
                'ON CONFLICT(workspace_id,principal_id) DO UPDATE SET '
                'realm=excluded.realm,issuer=excluded.issuer,subject=excluded.subject,generation=excluded.generation',
                (workspace_id, principal_id, realm, issuer, subject, generation))
        if connection.execute('SELECT 1 FROM operators WHERE principal_id=?', (principal_id,)).fetchone():
            connection.execute(
                'INSERT INTO operator_authority VALUES (?,?,?,?,?) '
                'ON CONFLICT(principal_id) DO UPDATE SET '
                'realm=excluded.realm,issuer=excluded.issuer,subject=excluded.subject,generation=excluded.generation',
                (principal_id, realm, issuer, subject, generation))


def simulate_identity_with_core_regrant(connection, statement, values=()):
    """Test-only identity change PLUS explicit core regrant, never module regrant."""
    before = dict(connection.execute('SELECT id,generation FROM principals').fetchall())
    result = simulate_identity(connection, statement, values)
    changed = [ident for ident, generation in connection.execute('SELECT id,generation FROM principals')
               if generation != before[ident]]
    bind_core_grants_for_test(connection, changed)
    return result
