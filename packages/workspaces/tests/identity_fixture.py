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
