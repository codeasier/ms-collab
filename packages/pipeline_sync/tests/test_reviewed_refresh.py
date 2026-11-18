# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic service-level failures at the new reviewed visibility boundary."""
from dataclasses import replace
from unittest import TestCase
from unittest.mock import patch

from packages.identity import Credential
from packages.pipeline_api import decode_snapshots, encode_snapshots, resource_id
from packages.pipeline_sync import ProviderError
from packages.pipeline_sync.tests._review_fixture import ReviewedFixture, SCOPE
from packages.workspaces import PolicyError


CONFIRM = "packages.pipeline_sync.service.review.confirm_replacement"
CLOCK = "packages.pipeline_sync.service.time.monotonic"
# Only a trusted responder test double; terminal challenge/answer is exercised
# without any patch in test_reviewed_terminal.py.
TEST_DECISION = "b" * 64


class ReviewedRefreshTests(ReviewedFixture, TestCase):
    def call(self, **changes):
        return self.sync.reviewed_refresh(**(self.args | changes))

    def test_named_admin_source_and_reader_policy_are_required_before_any_provider_read(self):
        policy = self.args["review_policy"]
        original = self.footprint()
        with patch(CONFIRM) as terminal, patch("packages.pipeline_sync.provider.HTTPSReadTransport.get") as network:
            for changes, error in [
                ({"user_credential": Credential("developer")}, PolicyError),
                ({"user_credential": Credential("reader")}, PolicyError),
                ({"review_policy": None}, ValueError),
                ({"review_policy": replace(policy, connection=replace(policy.connection, run_limit=1))}, ValueError),
                ({"review_policy": replace(policy, reviewer_pin=policy.reviewer_pin[:-1]+(2,))}, PolicyError),
                ({"review_policy": replace(policy, reviewer_epoch=2)}, PolicyError),
                ({"review_policy": replace(policy, membership_revision=2)}, PolicyError),
                ({"expected_revision": 2}, PolicyError),
            ]:
                with self.assertRaises(error):
                    self.call(**changes)
            with self.assertRaises(ValueError):
                replace(policy, audience="current-operator-only")
            class FalseTransport:
                def __bool__(self):
                    return False
                def get(self, *_):
                    raise AssertionError("false transport used")
            for transport in (None, FalseTransport(), object()):
                with patch.object(self.sync, "_transport", transport), self.assertRaises(ValueError):
                    self.call()
            self.assertEqual(terminal.call_count, 0, "review prompt must not be opened")
            self.assertEqual(network.call_count, 0, "implicit network must not be used")
        self.assertEqual(self.fake.calls, [])
        self.assertTrue(self.footprint()==original, "unexpected snapshot or update audit")

    def test_empty_candidates_never_reach_review_or_publish_but_legacy_empty_refresh_still_works(self):
        self.fake.items = []
        with patch(CONFIRM) as terminal:
            for initial in ("empty", "nonempty"):
                if initial == "nonempty":
                    self.seed_old()
                old = self.footprint()
                with self.assertRaisesRegex(ProviderError, "^empty_observation$"):
                    self.call()
                self.assertEqual(self.fake.calls, ["/api/v1/pipelines/build/runs?limit=5"])
                self.fake.calls.clear()
                self.assertTrue(self.footprint()==old, "snapshot or update audit changed")
            legacy = {k:v for k,v in self.args.items() if k != "review_policy"}
            legacy["user_credential"] = Credential("developer")
            result = self.sync.refresh(**legacy)
            self.assertEqual((result["revision"], result["run_count"]), (3, 0))
            self.assertFalse(result["full_history_complete"])
            self.assertTrue(decode_snapshots(SCOPE,self.footprint()[0].text)==[], "legacy empty publication differed")
            self.assertEqual(terminal.call_count, 0, "review prompt must not be opened")

    def test_provider_failure_and_obsolete_retained_observation_do_not_reach_terminal(self):
        with patch(CONFIRM) as terminal:
            old = self.footprint()
            with patch.object(self.fake, "detail", {"id": "different", "pipeline_id": "build"}):
                with self.assertRaises(ProviderError):
                    self.call()
            self.assertTrue(self.footprint()==old, "snapshot or update audit changed")
            # Legacy publication at the fixed observation time. A replacement
            # for that exact run at the same time is not newer.
            self.sync.refresh(**{k:v for k,v in self.args.items() if k != "review_policy"})
            old = self.footprint()
            with self.assertRaisesRegex(ProviderError, "^observation_not_newer$"):
                self.call(expected_revision=2)
            self.assertTrue(self.footprint()==old, "snapshot or update audit changed")
            self.assertEqual(terminal.call_count, 0, "review prompt must not be opened")

    def test_fresh_authority_before_screen_and_after_decision_fences_a_scoped_permission_cycle(self):
        for where in ("after collection", "during review"):
            # These transitions preserve the named admin's membership. The
            # scoped module revision still changed; it is not a valid old grant.
            revision = self.store.get_module("admin", "alpha", "pipelines").revision
            def cycle():
                for offset,state in enumerate(("disabling", "disabled", "enabled")):
                    self.store.set_module_state("admin", "alpha", "pipelines", state=state,
                                                expected_revision=revision+offset)
            exposed = []
            def review(*_, verifier):
                if where == "after collection":
                    cycle()
                verifier()
                exposed.append(True)
                if where == "during review":
                    cycle()
                return TEST_DECISION
            old = self.footprint()
            with self.subTest(where=where), patch(CONFIRM, side_effect=review) as prompt:
                with self.assertRaises(PolicyError) as failure:
                    self.call()
                self.assertEqual(failure.exception.code, "refresh_changed")
                self.assertEqual(prompt.call_count, 1)
                self.assertEqual(exposed, [] if where=="after collection" else [True])
            self.assertTrue(self.footprint()==old, "snapshot or update audit changed")

    def test_competing_writer_wins_while_operator_is_deciding_and_no_reviewed_write_is_retried(self):
        self.seed_old()
        winner = encode_snapshots(SCOPE, [])
        old, audits = self.footprint()
        def other_writer(*_, verifier):
            verifier()
            # An actual, separate transaction can publish while review waits.
            self.store.update_resource("admin", "alpha", "pipelines", resource_id(SCOPE),
                                       text=winner, expected_revision=old.revision)
            return TEST_DECISION
        with patch(CONFIRM, side_effect=other_writer) as prompt, self.assertRaises(PolicyError) as failure:
            self.call()
        self.assertEqual((failure.exception.status, failure.exception.code), (412, "revision_mismatch"))
        current, after = self.footprint()
        self.assertEqual(current.revision, old.revision+1)
        self.assertTrue(current.text==winner, "competing writer bytes were replaced")
        self.assertEqual(len(after), len(audits)+1)
        self.assertEqual(prompt.call_count, 1)
        self.assertEqual(len(self.fake.calls), 2)

    def test_missing_review_witness_or_invalid_decision_cannot_publish(self):
        old = self.footprint()
        with patch(CONFIRM, return_value=TEST_DECISION), self.assertRaisesRegex(ProviderError,"^review_expired$"):
            self.call()
        for result in (True, "B"*64, "yes"):
            def malformed(*_, verifier):
                verifier()
                return result
            with patch(CONFIRM, side_effect=malformed), self.assertRaisesRegex(ProviderError,"^review_not_confirmed$"):
                self.call()
        self.assertTrue(self.footprint()==old, "snapshot or update audit changed")

    def test_review_deadline_and_backwards_clock_roll_back_even_after_persistence(self):
        old = self.footprint()
        original = self.store._persist
        for where,value in (("before writer", 310.0), ("after persistence", 310.0),
                            ("after persistence", 9.99), ("after persistence", float("nan"))):
            clock = [10.0]
            persisted = []
            def accept(*_, verifier):
                verifier()
                if where == "before writer":
                    clock[0] = value
                return TEST_DECISION
            def persist(*a, **kw):
                original(*a, **kw)
                persisted.append(True)
                if where == "after persistence":
                    clock[0] = value
            with self.subTest(stage=where, time=value), patch(CONFIRM, side_effect=accept), \
                    patch(CLOCK, side_effect=lambda:clock[0]), patch.object(self.store, "_persist", side_effect=persist):
                with self.assertRaisesRegex(ProviderError, "^review_expired$"):
                    self.call()
            self.assertEqual(persisted, [True] if where=="after persistence" else [])
            self.assertTrue(self.footprint()==old, "snapshot or update audit changed")

    def test_same_transaction_readback_rejects_miswritten_snapshot_or_audit_and_rolls_both_back(self):
        self.seed_old()
        old = self.footprint()
        original = self.store._persist
        def accept(*_, verifier):
            verifier()
            return TEST_DECISION

        for table, code in (("resource", "refresh_changed"), ("audit", "refresh_commit_changed")):
            affected = []
            def faulty_storage(db, state, **options):
                original(db, state, **options)
                # Storage fault doubles modify only one expected, uncommitted
                # row in this disposable store. Revision and all ACLs remain.
                if table == "resource":
                    change = db.execute("UPDATE resources SET text=? WHERE workspace_id=? AND module_key=? AND id=? AND revision=?",
                                        (old[0].text, "alpha", "pipelines", resource_id(SCOPE), old[0].revision+1))
                else:
                    change = db.execute("UPDATE audit SET object_id=? WHERE workspace_id=? AND object_id=? AND revision=?",
                                        ("synthetic.storage.fault", "alpha", resource_id(SCOPE), old[0].revision+1))
                affected.append(change.rowcount)
            with self.subTest(table=table), patch(CONFIRM, side_effect=accept), \
                    patch.object(self.store, "_persist", side_effect=faulty_storage):
                with self.assertRaises(PolicyError) as failure:
                    self.call()
                self.assertEqual(failure.exception.code, code)
            self.assertEqual(affected, [1], "storage fault must hit the pending row")
            self.assertTrue(self.footprint()==old, "snapshot or update audit changed")
            self.assertTrue(self.store.audit_events("admin","alpha")==old[1], "unexpected audit change after rollback")
