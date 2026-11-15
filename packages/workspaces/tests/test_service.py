import dataclasses
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
import unittest

from packages.workspaces import (
    Membership, ModuleInstance, PolicyError, Principal, Resource, Workspace, WorkspaceService,
)


def fixture(*, two_admins=False):
    principals = tuple(Principal(p, "synthetic", p, p != "disabled")
                       for p in ("alpha_admin", "beta_admin", "multi", "viewer", "outsider", "operator", "disabled"))
    memberships = [Membership("alpha", "alpha_admin", "admin"),
                   Membership("alpha", "multi", "admin" if two_admins else "viewer"),
                   Membership("alpha", "viewer", "viewer"),
                   Membership("alpha", "disabled", "viewer"),
                   Membership("beta", "beta_admin", "admin"),
                   Membership("beta", "multi", "admin"),
                   Membership("commons", "beta_admin", "admin"),
                   Membership("commons", "viewer", "viewer")]
    return WorkspaceService(
        principals=principals,
        workspaces=(Workspace("alpha", "Alpha"), Workspace("beta", "Beta"), Workspace("commons", "Commons", "shared")),
        memberships=tuple(memberships),
        modules=(ModuleInstance("alpha", "board", ("read", "write")),
                 ModuleInstance("beta", "board", ("read", "write")),
                 ModuleInstance("commons", "board", ("read",)),
                 ModuleInstance("alpha", "notes", ("read",))),
        resources=(Resource("alpha", "board", "same", "Alpha data"),
                   Resource("beta", "board", "same", "Beta data"),
                   Resource("commons", "board", "public", "Explicit shared data"),
                   Resource("alpha", "notes", "same", "Alpha notes")),
        operators=frozenset({"operator"}),
    )


class ServiceTests(unittest.TestCase):
    def setUp(self):
        self.store = fixture()

    def error(self, status, code, fn, *args, **kwargs):
        with self.assertRaises(PolicyError) as caught:
            fn(*args, **kwargs)
        self.assertEqual((caught.exception.status, caught.exception.code), (status, code))
        self.assertEqual(str(caught.exception), code)

    def test_anonymous_unknown_disabled_principals_are_unauthenticated(self):
        for actor in (None, "missing", "disabled", ""):
            self.error(401, "unauthenticated", self.store.authorize, actor, "alpha", "board", "read")

    def test_issuer_subject_pair_is_unique_not_display_name(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            WorkspaceService(principals=(Principal("p1", "issuer", "same"), Principal("p2", "issuer", "same")),
                             workspaces=(), memberships=(), modules=())
        WorkspaceService(principals=(Principal("p1", "issuer1", "same"), Principal("p2", "issuer2", "same")),
                         workspaces=(), memberships=(), modules=())

    def test_unknown_and_unowned_workspace_are_indistinguishable(self):
        for workspace in ("beta", "missing", "commons"):
            self.error(404, "not_found", self.store.read_resource, "alpha_admin", workspace, "board", "same")
            self.error(404, "not_found", self.store.list_memberships, "alpha_admin", workspace)
            self.error(404, "not_found", self.store.set_membership, "alpha_admin", workspace, "beta_admin",
                       role="viewer", state="active", expected_revision=None)

    def test_shared_is_not_anonymous_or_implicit_membership(self):
        self.error(401, "unauthenticated", self.store.read_resource, None, "commons", "board", "public")
        self.error(404, "not_found", self.store.read_resource, "outsider", "commons", "board", "public")
        self.assertEqual(self.store.read_resource("viewer", "commons", "board", "public").text, "Explicit shared data")

    def test_space_list_contains_only_current_active_memberships(self):
        self.assertEqual([w.id for w in self.store.list_workspaces("multi")], ["alpha", "beta"])
        self.assertEqual(self.store.list_workspaces("outsider"), ())
        self.store.set_membership("alpha_admin", "alpha", "multi", role="viewer", state="revoked", expected_revision=1)
        self.assertEqual([w.id for w in self.store.list_workspaces("multi")], ["beta"])

    def test_role_in_other_workspace_cannot_elevate_viewer(self):
        self.error(403, "forbidden", self.store.update_resource, "multi", "alpha", "board", "same", text="bad", expected_revision=1)
        self.assertEqual(self.store.update_resource("multi", "beta", "board", "same", text="ok", expected_revision=1).text, "ok")
        self.assertEqual(self.store.read_resource("alpha_admin", "alpha", "board", "same").text, "Alpha data")

    def test_platform_operator_has_no_implicit_private_data_or_membership(self):
        self.assertEqual(self.store.list_workspaces("operator"), ())
        self.error(404, "not_found", self.store.read_resource, "operator", "alpha", "board", "same")
        self.error(404, "not_found", self.store.set_membership, "operator", "alpha", "operator", role="admin", state="active", expected_revision=1)

    def test_space_creation_is_atomic_with_explicit_initial_admin(self):
        workspace = self.store.create_workspace("operator", name="New synthetic", kind="product", initial_admin="outsider")
        self.assertNotIn(workspace, self.store.list_workspaces("operator"))
        self.assertEqual(self.store.list_memberships("outsider", workspace.id)[0].role, "admin")
        self.assertEqual(len(workspace.id), 36)
        self.error(403, "forbidden", self.store.create_workspace, "alpha_admin", name="No", kind="product", initial_admin="alpha_admin")
        self.error(400, "invalid_initial_admin", self.store.create_workspace, "operator", name="No", kind="shared", initial_admin="disabled")
        self.error(400, "invalid_workspace", self.store.create_workspace, "operator", name="", kind="shared", initial_admin="outsider")

    def test_composite_resource_identity_and_filtered_counts(self):
        self.assertEqual(self.store.read_resource("multi", "alpha", "board", "same").text, "Alpha data")
        self.assertEqual(self.store.read_resource("multi", "beta", "board", "same").text, "Beta data")
        self.assertEqual(self.store.read_resource("multi", "alpha", "notes", "same").text, "Alpha notes")
        total, page = self.store.list_resources("multi", "alpha", "board", limit=1)
        self.assertEqual(total, 1)
        self.assertEqual([(r.workspace_id, r.module_key) for r in page], [("alpha", "board")])
        self.assertEqual(self.store.list_resources("multi", "alpha", "board", offset=1), (1, ()))

    def test_foreign_resource_cannot_be_fetched_via_shared_space(self):
        self.error(404, "not_found", self.store.read_resource, "viewer", "commons", "board", "same")

    def test_unsupported_actions_and_module_caps_fail_closed(self):
        for action in ("delete", "execute", "*", ""):
            self.error(403, "forbidden", self.store.authorize, "alpha_admin", "alpha", "board", action)
        self.error(403, "forbidden", self.store.update_resource, "beta_admin", "commons", "board", "public", text="no", expected_revision=1)

    def test_immutable_outputs_do_not_mutate_service(self):
        resource = self.store.read_resource("viewer", "alpha", "board", "same")
        with self.assertRaises(dataclasses.FrozenInstanceError):
            resource.text = "bad"
        module = self.store.get_module("viewer", "alpha", "board")
        self.assertIsInstance(module.capabilities, tuple)
        with self.assertRaises(dataclasses.FrozenInstanceError):
            module.state = "disabled"

    def test_page_shape_checks_after_authorization(self):
        for kwargs in ({"offset": -1}, {"limit": 0}, {"limit": True}, {"limit": 101}, {"offset": 0.5}):
            self.error(400, "invalid_page", self.store.list_resources, "viewer", "alpha", "board", **kwargs)
        self.error(404, "not_found", self.store.list_resources, "outsider", "alpha", "board", limit=0)

    def test_mutation_revision_required_and_stale_request_rejected(self):
        self.error(428, "precondition_required", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text="x", expected_revision=None)
        for revision in (True, 0, "1"):
            self.error(400, "invalid_revision", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text="x", expected_revision=revision)
        self.store.update_resource("alpha_admin", "alpha", "board", "same", text="new", expected_revision=1)
        self.error(412, "stale_revision", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text="old", expected_revision=1)
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").text, "new")

    def test_only_admin_manages_members_and_modules(self):
        self.error(403, "forbidden", self.store.list_memberships, "viewer", "alpha")
        self.error(403, "forbidden", self.store.set_module_state, "viewer", "alpha", "board", state="disabling", expected_revision=1)
        self.error(403, "forbidden", self.store.set_membership, "viewer", "alpha", "viewer", role="admin", state="active", expected_revision=1)

    def test_member_edit_cannot_change_owner_or_grant_platform_role(self):
        self.error(404, "not_found", self.store.set_membership, "alpha_admin", "alpha", "beta_admin", role="admin", state="active", expected_revision=1)
        self.error(400, "invalid_membership", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="operator", state="active", expected_revision=1)
        self.error(400, "invalid_membership", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="viewer", state="invited", expected_revision=1)

    def test_last_admin_revoke_or_demote_rejected(self):
        for role, state in (("viewer", "active"), ("admin", "revoked")):
            self.error(409, "last_admin", self.store.set_membership, "alpha_admin", "alpha", "alpha_admin", role=role, state=state, expected_revision=1)
        self.assertEqual(self.store.list_memberships("alpha_admin", "alpha")[0].role, "admin")
        self.assertEqual(self.store.audit_events("alpha_admin", "alpha"), ())

    def test_inactive_member_cannot_be_promoted_to_satisfy_admin_invariant(self):
        self.error(409, "principal_inactive", self.store.set_membership, "alpha_admin", "alpha", "disabled", role="admin", state="active", expected_revision=1)

    def test_admin_transfer_is_atomic_and_scoped(self):
        demoted, promoted = self.store.transfer_admin("alpha_admin", "alpha", "viewer", expected_revision=1, replacement_revision=1)
        self.assertEqual((demoted.role, promoted.role), ("developer", "admin"))
        self.error(403, "forbidden", self.store.list_memberships, "alpha_admin", "alpha")
        self.assertEqual(len(self.store.list_memberships("viewer", "alpha")), 4)
        self.error(404, "not_found", self.store.transfer_admin, "viewer", "alpha", "beta_admin", expected_revision=2, replacement_revision=1)

    def test_admin_transfer_preconditions_do_not_half_apply(self):
        self.error(412, "stale_revision", self.store.transfer_admin, "alpha_admin", "alpha", "viewer", expected_revision=1, replacement_revision=99)
        self.assertEqual(self.store.list_memberships("alpha_admin", "alpha")[0].role, "admin")
        self.error(409, "replacement_ineligible", self.store.transfer_admin, "alpha_admin", "alpha", "disabled", expected_revision=1, replacement_revision=1)
        self.error(409, "replacement_ineligible", self.store.transfer_admin, "alpha_admin", "alpha", "alpha_admin", expected_revision=1, replacement_revision=1)

    def test_concurrent_admin_demotion_cannot_remove_last_admin(self):
        store = fixture(two_admins=True)
        barrier = Barrier(2)
        def demote(actor):
            barrier.wait()
            try:
                store.set_membership(actor, "alpha", actor, role="developer", state="active", expected_revision=1)
                return "ok"
            except PolicyError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            results = list(pool.map(demote, ("alpha_admin", "multi")))
        self.assertCountEqual(results, ["ok", "last_admin"])
        surviving = "alpha_admin" if results[0] != "ok" else "multi"
        self.assertEqual(sum(m.role == "admin" and m.state == "active" for m in store.list_memberships(surviving, "alpha")), 1)

    def test_revoked_membership_is_not_reinstated_by_edit(self):
        self.store.set_membership("alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        self.error(404, "not_found", self.store.read_resource, "viewer", "alpha", "board", "same")
        self.error(409, "membership_revoked", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="viewer", state="active", expected_revision=2)

    def test_prior_decision_does_not_authorize_after_revocation_barrier(self):
        store = fixture(two_admins=True)
        observed = Event()
        revoked = Event()
        def worker():
            decision = store.authorize("multi", "alpha", "board", "write")
            observed.set()
            if not revoked.wait(5):
                raise AssertionError("revocation barrier not reached")
            try:
                store.update_resource(decision.principal_id, decision.workspace_id, decision.module_key, "same", text="late write", expected_revision=1)
            except PolicyError as error:
                return error.status, error.code
            return None
        with ThreadPoolExecutor(1) as pool:
            pending = pool.submit(worker)
            self.assertTrue(observed.wait(5))
            store.set_membership("alpha_admin", "alpha", "multi", role="admin", state="revoked", expected_revision=1)
            revoked.set()
            self.assertEqual(pending.result(5), (404, "not_found"))
        self.assertEqual(store.read_resource("alpha_admin", "alpha", "board", "same").text, "Alpha data")

    def test_module_disabling_denies_immediately_and_does_not_affect_other_space(self):
        before = self.store.authorize("viewer", "alpha", "board", "read")
        self.store.set_module_state("alpha_admin", "alpha", "board", state="disabling", expected_revision=1)
        self.error(409, "module_unavailable", self.store.read_resource, "viewer", "alpha", "board", "same")
        self.error(409, "module_unavailable", self.store.get_module, "viewer", "alpha", "board")
        self.assertEqual(self.store.get_module("alpha_admin", "alpha", "board").state, "disabling")
        self.assertEqual(self.store.read_resource("multi", "beta", "board", "same").text, "Beta data")
        self.store.set_module_state("alpha_admin", "alpha", "board", state="disabled", expected_revision=2)
        self.store.set_module_state("alpha_admin", "alpha", "board", state="enabled", expected_revision=3)
        self.assertGreater(self.store.authorize("viewer", "alpha", "board", "read").policy_epoch, before.policy_epoch)

    def test_reenable_does_not_restore_revoked_membership(self):
        self.store.set_membership("alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        self.store.set_module_state("alpha_admin", "alpha", "board", state="disabling", expected_revision=1)
        self.store.set_module_state("alpha_admin", "alpha", "board", state="disabled", expected_revision=2)
        self.store.set_module_state("alpha_admin", "alpha", "board", state="enabled", expected_revision=3)
        self.error(404, "not_found", self.store.read_resource, "viewer", "alpha", "board", "same")

    def test_concurrent_stale_updates_have_one_winner(self):
        barrier = Barrier(2)
        def write(value):
            barrier.wait()
            try:
                self.store.update_resource("alpha_admin", "alpha", "board", "same", text=value, expected_revision=1)
                return "ok"
            except PolicyError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(write, ("first", "second"))), ["ok", "stale_revision"])
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").revision, 2)
        self.assertEqual(len(self.store.audit_events("alpha_admin", "alpha")), 1)

    def test_module_lifecycle_cannot_skip_disabling(self):
        self.error(409, "invalid_transition", self.store.set_module_state, "alpha_admin", "alpha", "board", state="disabled", expected_revision=1)

    def test_workspace_lifecycle_is_platform_only(self):
        self.error(403, "forbidden", self.store.set_workspace_state, "alpha_admin", "alpha", state="suspended", expected_revision=1)
        self.store.set_workspace_state("operator", "alpha", state="suspended", expected_revision=1)
        self.error(409, "workspace_unavailable", self.store.read_resource, "viewer", "alpha", "board", "same")
        self.error(409, "workspace_unavailable", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="developer", state="active", expected_revision=1)
        self.assertEqual([w.id for w in self.store.list_workspaces("multi")], ["beta"])
        self.store.set_workspace_state("operator", "alpha", state="active", expected_revision=2)
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").text, "Alpha data")

    def test_archive_retains_authorized_local_reads_but_no_writes_or_reactivation(self):
        self.store.set_workspace_state("operator", "alpha", state="archived", expected_revision=1)
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").text, "Alpha data")
        self.error(409, "workspace_unavailable", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text="no", expected_revision=1)
        self.error(409, "invalid_transition", self.store.set_workspace_state, "operator", "alpha", state="active", expected_revision=2)

    def test_archived_access_can_be_revoked_without_restoring_writes(self):
        self.store.set_workspace_state("operator", "alpha", state="archived", expected_revision=1)
        self.assertEqual(len(self.store.list_memberships("alpha_admin", "alpha")), 4)
        self.store.set_membership("alpha_admin", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        self.error(404, "not_found", self.store.read_resource, "viewer", "alpha", "board", "same")
        self.error(404, "not_found", self.store.list_resources, "viewer", "alpha", "board")
        self.assertEqual([w.id for w in self.store.list_workspaces("viewer")], ["commons"])
        self.assertEqual(self.store.audit_events("alpha_admin", "alpha")[-1].action, "membership.update")
        self.error(409, "membership_revoked", self.store.set_membership, "alpha_admin", "alpha", "viewer", role="viewer", state="active", expected_revision=2)

    def test_archive_membership_controls_cannot_expand_authority(self):
        store = fixture(two_admins=True)
        store.set_workspace_state("operator", "alpha", state="archived", expected_revision=1)
        self.error(409, "archive_privilege_increase", store.set_membership, "alpha_admin", "alpha", "viewer", role="developer", state="active", expected_revision=1)
        self.error(409, "archive_privilege_increase", store.set_membership, "alpha_admin", "alpha", "viewer", role="admin", state="revoked", expected_revision=1)
        self.error(409, "workspace_unavailable", store.transfer_admin, "alpha_admin", "alpha", "viewer", expected_revision=1, replacement_revision=1)
        self.error(403, "forbidden", store.set_membership, "viewer", "alpha", "viewer", role="viewer", state="revoked", expected_revision=1)
        store.set_membership("alpha_admin", "alpha", "multi", role="viewer", state="active", expected_revision=1)
        self.error(409, "last_admin", store.set_membership, "alpha_admin", "alpha", "alpha_admin", role="viewer", state="revoked", expected_revision=1)

    def test_concurrent_archived_admin_revoke_keeps_last_admin(self):
        store = fixture(two_admins=True)
        store.set_workspace_state("operator", "alpha", state="archived", expected_revision=1)
        barrier = Barrier(2)
        def revoke(actor):
            barrier.wait()
            try:
                store.set_membership(actor, "alpha", actor, role="admin", state="revoked", expected_revision=1)
                return "ok"
            except PolicyError as error:
                return error.code
        with ThreadPoolExecutor(2) as pool:
            self.assertCountEqual(list(pool.map(revoke, ("alpha_admin", "multi"))), ["ok", "last_admin"])

    def test_audit_is_scoped_metadata_not_resource_payload(self):
        self.store.update_resource("alpha_admin", "alpha", "board", "same", text="synthetic-content-not-audit", expected_revision=1)
        events = self.store.audit_events("alpha_admin", "alpha")
        self.assertEqual(events[0].action, "resource.update")
        self.assertNotIn("synthetic-content", repr(events))
        self.assertEqual(self.store.audit_events("beta_admin", "beta"), ())
        self.error(404, "not_found", self.store.audit_events, "operator", "alpha")
        self.error(403, "forbidden", self.store.audit_events, "viewer", "alpha")

    def test_unknown_module_and_resource_fail_closed(self):
        self.error(404, "not_found", self.store.authorize, "viewer", "alpha", "missing", "read")
        self.error(404, "not_found", self.store.read_resource, "viewer", "alpha", "board", "missing")

    def test_invalid_resource_change_is_atomic(self):
        self.error(400, "invalid_resource", self.store.update_resource, "alpha_admin", "alpha", "board", "same", text={}, expected_revision=1)
        self.assertEqual(self.store.read_resource("viewer", "alpha", "board", "same").revision, 1)


class BootstrapTests(unittest.TestCase):
    def test_invalid_model_values_rejected(self):
        invalid = [lambda: Workspace("", "bad"), lambda: Workspace("w", "bad", "public"),
                   lambda: Membership("w", "p", "superuser"),
                   lambda: ModuleInstance("w", "m", ["read"]),
                   lambda: ModuleInstance("w", "m", ("execute",)),
                   lambda: Resource("w", "m", "r", "text", revision=True),
                   lambda: Principal("p", "i", "s", active=1)]
        for make in invalid:
            with self.assertRaises(ValueError):
                make()

    def test_no_admin_or_inactive_admin_bootstrap_rejected(self):
        for active, role in ((False, "admin"), (True, "viewer")):
            with self.assertRaisesRegex(ValueError, "active eligible admin"):
                WorkspaceService(principals=(Principal("p", "i", "s", active),), workspaces=(Workspace("w", "W"),),
                                 memberships=(Membership("w", "p", role),), modules=())

    def test_foreign_reference_and_duplicate_keys_rejected(self):
        base = dict(principals=(Principal("p", "i", "s"),), workspaces=(Workspace("w", "W"),),
                    memberships=(Membership("w", "p", "admin"),), modules=(ModuleInstance("w", "m"),))
        cases = [dict(memberships=(Membership("w", "missing", "admin"),)),
                 dict(modules=(ModuleInstance("other", "m"),)),
                 dict(resources=(Resource("other", "m", "r", "text"),)),
                 dict(operators=frozenset({"other"})),
                 dict(workspaces=(Workspace("w", "W"), Workspace("w", "Other"))),
                 dict(resources=(Resource("w", "m", "r", "1"), Resource("w", "m", "r", "2")))]
        for extra in cases:
            with self.assertRaises(ValueError):
                WorkspaceService(**(base | extra))


if __name__ == "__main__":
    unittest.main()
