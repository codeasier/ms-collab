"""Synthetic directory/verifier only; actual SQLite transactions and WSGI."""
from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
import sqlite3
from unittest.mock import patch
import unittest
from uuid import uuid4

from packages.workspace_api.tests import test_application as fixtures
from packages.workspace_admin_api import (ModuleTemplate, WorkspaceAdministrationApplication,
                                         initialize_administration)
from packages.workspaces import SQLiteWorkspaceService, StorageError

CONTROL = "/api/control/workspaces"
MODULES = "/api/workspaces/alpha/modules"


class AdministrationTests(unittest.TestCase):
    def setUp(self):
        fixtures.ApplicationTests.setUp(self)
        self.base_api = self.app
        initialize_administration(self.store)
        self.app = WorkspaceAdministrationApplication(api=self.base_api, module_catalog=(
            ModuleTemplate("board", ("read", "write")), ModuleTemplate("pipeline", ("read",))))

    def request(self, path=CONTROL, *, key=None, **kwargs):
        extra = kwargs.pop("extra", {})
        if key is not None:
            extra["HTTP_IDEMPOTENCY_KEY"] = key
        return fixtures.call(self.app, path, extra=extra, **kwargs)

    def create(self, *, key=None, **kwargs):
        return self.request(method="POST", key=key or str(uuid4()),
                            body=kwargs.pop("body", {"name": "New space", "kind": "product", "initial_admin": "other"}), **kwargs)

    def install(self, *, key=None, **kwargs):
        return self.request(MODULES, method="POST", key=key or str(uuid4()),
                            body=kwargs.pop("body", {"key": "pipeline"}), **kwargs)

    def test_factory_catalog_is_trusted_bounded_configuration(self):
        for value in (None, object(), self.app):
            with self.assertRaises(ValueError):
                WorkspaceAdministrationApplication(api=value)
        for key, caps in (("../bad", ("read",)), ("board", ["read"]), ("board", ()),
                          ("board", ("read", "read")), ("board", ("execute",)), ("board", ([],))):
            with self.assertRaises(ValueError):
                ModuleTemplate(key, caps)
        with self.assertRaises(ValueError):
            WorkspaceAdministrationApplication(api=self.base_api, module_catalog=(ModuleTemplate("x"), ModuleTemplate("x")))
        with self.assertRaises(ValueError):
            WorkspaceAdministrationApplication(api=self.base_api, module_catalog=({"key": "x"},))

    def test_initialization_is_explicit_and_repeatable(self):
        initialize_administration(self.store)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE workspace_admin_requests_v2")
        self.assertEqual(self.create()["status"], 503)
        initialize_administration(self.store)
        self.assertEqual(self.create()["status"], 200)

    def test_unknown_or_replaced_retry_schema_fails_closed(self):
        from packages.workspace_admin_api.requests import _CREATE
        candidates = ["CREATE TABLE workspace_admin_requests_v2(actor,action,scope,request_key,digest,result_id)",
                      _CREATE.replace("workspace.create", "WORKSPACE.CREATE"),
                      _CREATE.replace(" STRICT", ""),
                      _CREATE.replace(" NOT NULL", ""),
                      _CREATE.replace(" REFERENCES principals(id)", ""),
                      _CREATE.replace("PRIMARY KEY(actor,action,scope,request_key)", "PRIMARY KEY(action,scope,request_key)"),
                      _CREATE.replace(" CHECK(action IN ('workspace.create','module.install'))", "")]
        for definition in candidates:
            with self.subTest(definition=definition):
                with sqlite3.connect(self.path) as db:
                    db.execute("DROP TABLE workspace_admin_requests_v2")
                    db.execute(definition)
                with self.assertRaises(StorageError):
                    initialize_administration(self.store)
                self.assertEqual(self.create()["status"], 503)
                self.assertEqual(len(self.store.list_control_workspaces("admin")), 3)
        with sqlite3.connect(self.path) as db:
            db.execute("DROP TABLE workspace_admin_requests_v2")
        initialize_administration(self.store)
        self.assertEqual(self.create()["status"], 200)

    def test_authentication_and_capabilities_are_not_client_claims(self):
        for token, expected in ((None, 401), ("bad", 401), ("test-disabled", 401)):
            self.assertEqual(self.request("/api/control/capabilities", token=token)["status"], expected)
        for token, expected in (("test-admin", True), ("test-viewer", False), ("test-other", False)):
            result = self.request("/api/control/capabilities", token=token, extra={"HTTP_X_ROLE": "operator"})
            self.assertEqual(result["body"]["data"], {"manage_workspaces": expected})

    def test_operator_lifecycle_metadata_does_not_grant_business_access(self):
        spaces = self.request()["body"]["data"]
        self.assertEqual({w["id"] for w in spaces}, {"alpha", "beta", "commons"})
        self.assertEqual(set(spaces[0]), {"id", "name", "kind", "state", "revision"})
        self.assertEqual(self.request("/api/workspaces/beta/members")["status"], 404)
        self.assertEqual(self.request("/api/workspaces/beta/modules")["status"], 404)

    def test_nonoperator_cannot_list_create_or_change_lifecycle(self):
        for token in ("test-viewer", "test-other", "test-outsider"):
            self.assertEqual(self.request(token=token)["status"], 403)
            self.assertEqual(self.create(token=token)["status"], 403)
            self.assertEqual(self.request(CONTROL + "/alpha", method="PATCH", token=token,
                                         body={"state": "archived"}, revision='"1"')["status"], 403)

    def test_create_both_kinds_with_explicit_initial_admin_not_operator(self):
        for kind in ("product", "shared"):
            result = self.create(body={"name": "Created", "kind": kind, "initial_admin": "other"})
            self.assertEqual(result["status"], 200)
            w = result["body"]["data"]
            self.assertEqual((w["kind"], w["state"], w["revision"]), (kind, "active", 1))
            self.assertNotIn(w["id"], {s.id for s in self.store.list_workspaces("admin")})
            members = self.store.list_memberships("other", w["id"])
            self.assertEqual([(m.principal_id, m.role) for m in members], [("other", "admin")])
            self.assertEqual(self.store.list_modules("other", w["id"]), ())

    def test_create_rejects_unverified_inactive_and_client_supplied_authority(self):
        for principal in ("missing", "disabled"):
            self.assertEqual(self.create(body={"name": "X", "kind": "product", "initial_admin": principal})["status"], 400)
        for body in ({"name": "X", "kind": "public", "initial_admin": "admin"},
                     {"name": "", "kind": "product", "initial_admin": "admin"},
                     {"name": "X", "kind": "product", "initial_admin": "admin", "operator": True},
                     {"name": "X", "kind": "product", "initial_admin": "admin", "id": "chosen"}):
            self.assertEqual(self.create(body=body)["status"], 400)
        self.assertEqual(len(self.store.list_control_workspaces("admin")), 3)

    def test_idempotency_is_required_canonical_uuid4(self):
        for key in (None, "", "short", "00000000-0000-0000-0000-000000000000", str(uuid4()).upper()):
            self.assertEqual(self.request(method="POST", key=key,
                body={"name": "X", "kind": "product", "initial_admin": "admin"})["status"], 400)

    def test_creation_replay_persists_and_conflicting_body_is_rejected(self):
        key = str(uuid4())
        first = self.create(key=key)["body"]["data"]
        self.store = SQLiteWorkspaceService(self.path)
        self.base_api._store = self.store
        self.assertEqual(self.create(key=key)["body"]["data"], first)
        self.assertEqual(self.create(key=key, body={"name": "Different", "kind": "product", "initial_admin": "other"})["status"], 409)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM audit WHERE action='workspace.create'").fetchone()[0], 1)

    def test_concurrent_duplicate_create_has_one_workspace_and_audit(self):
        key = str(uuid4())
        with ThreadPoolExecutor(max_workers=2) as executor:
            results = list(executor.map(lambda _: self.create(key=key), range(2)))
        self.assertTrue(all(r["status"] == 200 for r in results))
        self.assertEqual(results[0]["body"], results[1]["body"])
        self.assertEqual(len(self.store.list_control_workspaces("admin")), 4)

    def test_stale_operator_pin_is_not_advertised_as_capability(self):
        with sqlite3.connect(self.path) as db:
            db.execute("UPDATE operator_authority SET generation=2 WHERE principal_id='admin'")
        result = self.request('/api/control/capabilities')
        self.assertEqual(result['status'],200)
        self.assertEqual(result['body']['data'],{'manage_workspaces':False})
        self.assertEqual(self.create()['status'],403)

    def test_replay_rechecks_operator_authority(self):
        key = str(uuid4())
        self.create(key=key)
        with sqlite3.connect(self.path) as db:
            db.execute("DELETE FROM operator_authority")
            db.execute("DELETE FROM operators")
        self.assertEqual(self.create(key=key)["status"], 403)

    def test_operator_revocation_during_verification_is_observed(self):
        def revoke():
            with sqlite3.connect(self.path) as db:
                db.execute("DELETE FROM operator_authority")
                db.execute("DELETE FROM operators")
        self.verifier.hook = revoke
        self.assertEqual(self.create()["status"], 403)

    def test_expiry_rolls_back_create_idempotency_and_audit(self):
        from packages.workspace_admin_api import application
        original = application.remember
        def expire(*args):
            original(*args)
            self.now = fixtures.NOW + timedelta(minutes=2)
        with patch.object(application, "remember", side_effect=expire):
            self.assertEqual(self.create()["status"], 401)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workspace_admin_requests_v2").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM audit").fetchone()[0], 0)
            self.assertEqual(db.execute("SELECT count(*) FROM workspaces").fetchone()[0], 3)

    def test_persistence_failure_does_not_leave_creation_retry_record(self):
        with patch.object(self.store, "_persist", side_effect=StorageError("test")):
            self.assertEqual(self.create()["status"], 503)
        with sqlite3.connect(self.path) as db:
            self.assertEqual(db.execute("SELECT count(*) FROM workspace_admin_requests_v2").fetchone()[0], 0)

    def test_lifecycle_revision_suspend_recover_and_irreversible_archive(self):
        path = CONTROL + "/alpha"
        self.assertEqual(self.request(path, method="PATCH", body={"state": "archived"})["status"], 428)
        for state, rev in (("suspended", 1), ("active", 2), ("archived", 3)):
            result = self.request(path, method="PATCH", body={"state": state}, revision=f'"{rev}"')
            self.assertEqual(result["status"], 200)
            self.assertEqual(result["body"]["data"]["revision"], rev + 1)
            if state == "suspended":
                self.assertEqual(self.request(MODULES)["status"], 409)
                self.assertIn("alpha", {w["id"] for w in self.request()["body"]["data"]})
        self.assertEqual(self.request(path, method="PATCH", body={"state": "active"}, revision='"4"')["status"], 409)
        self.assertEqual(self.request(path, method="PATCH", body={"state": "active"}, revision='"1"')["status"], 412)

    def test_catalog_and_module_lists_require_this_workspace_admin(self):
        for path in (MODULES, "/api/workspaces/alpha/module-catalog"):
            self.assertEqual(self.request(path, token="test-viewer")["status"], 403)
            self.assertEqual(self.request(path, token="test-other")["status"], 404)
            self.assertEqual(self.request(path)["status"], 200)
        self.assertEqual(self.request("/api/workspaces/missing/modules")["status"], 404)

    def test_module_installs_disabled_only_from_server_catalog(self):
        result = self.install()
        self.assertEqual(result["status"], 200)
        self.assertEqual(result["body"]["data"], {"workspace_id": "alpha", "key": "pipeline",
                         "capabilities": ["read"], "state": "disabled", "revision": 1})
        self.assertEqual(SQLiteWorkspaceService(self.path).get_module("admin", "alpha", "pipeline").state, "disabled")
        self.assertEqual(self.install()["status"], 409)
        self.assertEqual(self.install(body={"key": "unapproved"})["status"], 400)
        self.assertEqual(self.install(body={"key": "x", "capabilities": "write"})["status"], 400)
        self.assertEqual(self.install(token="test-viewer")["status"], 403)

    def test_install_replay_does_not_restore_old_module_state(self):
        key = str(uuid4())
        self.install(key=key)
        path = MODULES + "/pipeline"
        result = self.request(path, method="PATCH", body={"state": "enabled"}, revision='"1"')
        self.assertEqual(result["status"], 200)
        self.assertEqual(self.install(key=key)["body"]["data"]["state"], "enabled")
        self.assertEqual(self.install(key=key, body={"key": "board"})["status"], 409)

    def test_module_lifecycle_is_revision_checked_and_reenable_is_catalog_bounded(self):
        path = MODULES + "/board"
        self.assertEqual(self.request(path, method="PATCH", body={"state": "disabled"}, revision='"1"')["status"], 409)
        self.assertEqual(self.request(path, method="PATCH", body={"state": "disabling"}, revision='"1"')["status"], 200)
        self.assertEqual(self.request(fixtures.BASE + "/same")["status"], 409)
        self.assertEqual(self.request(path, method="PATCH", body={"state": "disabled"}, revision='"1"')["status"], 412)
        self.assertEqual(self.request(path, method="PATCH", body={"state": "disabled"}, revision='"2"')["status"], 200)
        self.app = WorkspaceAdministrationApplication(api=self.base_api, module_catalog=(ModuleTemplate("board"),))
        self.assertEqual(self.request(path, method="PATCH", body={"state": "enabled"}, revision='"3"')["status"], 409)
        self.app = WorkspaceAdministrationApplication(api=self.base_api, module_catalog=(ModuleTemplate("board", ("read", "write")),))
        self.assertEqual(self.request(path, method="PATCH", body={"state": "enabled"}, revision='"3"')["status"], 200)

    def test_archive_has_read_only_module_metadata_and_no_new_install_or_enable(self):
        self.store.set_workspace_state("admin", "alpha", state="archived", expected_revision=1)
        self.assertEqual(self.request(MODULES)["status"], 200)
        self.assertEqual(self.install()["status"], 409)
        self.assertEqual(self.request(MODULES + "/board", method="PATCH", body={"state": "disabling"}, revision='"1"')["status"], 409)

    def test_unknown_routes_queries_and_extra_fields_fail_closed(self):
        for path in ("/api/control", "/api/control/principals", "/api/control/workspaces/alpha/extra", "/api/workspaces/alpha/modules/../../beta"):
            self.assertIn(self.request(path)["status"], {400, 404})
        self.assertEqual(self.request(query="actor=admin")["status"], 400)
        self.assertEqual(self.request(CONTROL + "/alpha", method="PATCH", revision='"1"',
                                     body={"state": "archived", "workspace": "beta"})["status"], 400)
        result = self.request(CONTROL + "/alpha", method="PATCH", revision='"1"',
                              raw=b'{"state":"archived","state":"active"}')
        self.assertEqual(result["status"], 400)


if __name__ == "__main__":
    unittest.main()
