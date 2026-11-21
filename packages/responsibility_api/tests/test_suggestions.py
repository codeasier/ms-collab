# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic prepared Issue suggestions behind current transactional policy."""

from dataclasses import replace
from datetime import timedelta
from os import getpid
import unittest
from unittest.mock import patch

from packages.community_api import replace_policy as replace_community_policy
from packages.community_api.records import fingerprint
from packages.community_api.tests import test_application as community
from packages.community_board.model import BoardItem, Scope, Snapshot
from packages.identity import Credential, Provider
from packages.responsibility.suggestions import IssueSuggestion, MAX_ISSUE_NUMBER
from packages.responsibility_api import ResponsibilityApplication
from packages.responsibility_api.tests import test_application as base
from packages.workspaces import PolicyError, SQLiteWorkspaceService


class SuggestionTests(unittest.TestCase):
    cstate = base.ResponsibilityTests.cstate
    rstate = base.ResponsibilityTests.rstate
    request = base.ResponsibilityTests.request
    save = base.ResponsibilityTests.save
    count_audit = base.ResponsibilityTests.count_audit

    def setUp(self):
        base.ResponsibilityTests.setUp(self)
        self.preparer = self.app
        self.scope = community.SCOPE
        self.record = self._record(self.scope)

    def _provider_settings(self):
        return dict(providers=(Provider("test", community.ISSUER, "community-test", self.verifier),),
                    provider_key="test", clock=lambda: self.now)

    def _attach(self, *batches, store=None):
        self.app = ResponsibilityApplication(
            store=self.store if store is None else store, prepared_suggestions=batches,
            **self._provider_settings()
        )

    def _record(self, scope, number=1, *, kind="issue", field_ids=("ui",)):
        saved = self.store._transaction(lambda _, db: community.read(db, scope), write=False, with_connection=True)
        item = next(row for row in saved.snapshot.items if row.key == (kind, number))
        return IssueSuggestion(number, fingerprint(scope, item), field_ids)

    def _prepare(self, *records, scope=None, token="test-admin", **kwargs):
        scope = self.scope if scope is None else scope
        saved = self.store._transaction(lambda _, db: community.read(db, scope), write=False, with_connection=True)
        kwargs.setdefault("expected_revision", self.rstate(scope)["revision"])
        kwargs.setdefault("community_revision", saved.revision)
        return self.preparer.prepare_issue_suggestions(Credential(token), scope, records, **kwargs)

    def _suggestion(self, number=1, **kwargs):
        return self.request("/suggestion", query="number=" + str(number), **kwargs)

    def _publish_community(self, scope, items, approved_keys, *, approver="test-admin"):
        path = f"/api/workspaces/{scope.workspace_id}/community/{scope.connection_id}/{scope.repository}"
        def state():
            return self.store._transaction(lambda _, db: community.read(db, scope), write=False, with_connection=True)
        def observe(connection, credential, *, observed_at, before_read=None):
            return Snapshot(connection.scope, observed_at, items)
        self.now += timedelta(seconds=1)
        with patch.object(self.provider, "observe", side_effect=observe):
            self.assertEqual(community.call(self.community, "/refresh", base=path, method="POST",
                token="test-admin", revision=state().revision, body={})["status"], 200)
        self.assertEqual(community.call(self.community, "/approve", base=path, method="POST",
            token=approver, revision=state().revision,
            body={"fingerprints": [fingerprint(scope, row) for row in items if row.key in approved_keys]})["status"], 200)

    def _denied(self, result):
        self.assertEqual((result["status"], result["body"]), (404, {"error": "not_found"}))

    def test_value_is_bounded_ordered_and_does_not_treat_text_as_a_field(self):
        self.assertEqual(IssueSuggestion(1, "a" * 64, ("core", "ui")).field_ids, ("core", "ui"))
        for n, digest, fields in (
            (True, "a" * 64, ("ui",)), (0, "a" * 64, ("ui",)),
            (MAX_ISSUE_NUMBER + 1, "a" * 64, ("ui",)), (1, "A" * 64, ("ui",)),
            (1, "a" * 64, ()), (1, "a" * 64, ("ui", "ui")),
            (1, "a" * 64, ["ui"]), (1, "a" * 64, ("a", "b", "c", "d")),
            (1, "a" * 64, ("../free text",)),
        ):
            with self.subTest(n=n, fields=fields), self.assertRaises(ValueError):
                IssueSuggestion(n, digest, fields)

    def test_exact_reader_get_has_no_business_mutation_assignment_or_provider_call(self):
        self.assertEqual(self.save()["status"], 200)
        before = (self.cstate(), self.rstate(), self.count_audit())
        all_before = self.request("/candidates", query="view=all")
        mine_before = self.request("/candidates", query="view=mine")
        # Authority alone does not mount a route or even parse its query.
        for query in ("number=1", "number=bogus", "number=1&number=1", "number=" + "1" * 401):
            self._denied(self.request("/suggestion", query=query))
        with patch.object(self.provider, "observe", side_effect=AssertionError("unexpected acquisition")) as observe:
            self._attach(self._prepare(replace(self.record, field_ids=("core", "ui"))))
            result = self._suggestion()
            self.assertEqual(result["status"], 200, result)
            self.assertEqual(result["headers"]["Cache-Control"], "no-store")
            self.assertEqual(result["body"]["data"], {
                "scope": self.scope.as_dict(), "number": 1, "field_ids": ["core", "ui"],
                "community_revision": before[0].revision, "config_version": before[1]["config_version"],
                "mode": "suggestion_only",
            })
            self._denied(self.request("/suggestion", query="number=1", method="POST",
                                      token="test-admin", body={"field_ids": ["ui"]}))
            self.assertEqual(self.request("/candidates", query="view=all"), all_before)
            self.assertEqual(self.request("/candidates", query="view=mine"), mine_before)
            observe.assert_not_called()
        self.assertEqual((self.cstate(), self.rstate(), self.count_audit()), before)

    def test_preparation_requires_current_publisher_config_and_exact_approved_open_issue(self):
        with self.assertRaises(PolicyError) as empty:
            self._prepare(self.record)
        self.assertEqual(empty.exception.code, "suggestion_configuration_unavailable")
        self.assertEqual(self.save()["status"], 200)
        for token in ("test-reader", "test-second"):
            with self.subTest(token=token), self.assertRaises(PolicyError):
                self._prepare(self.record, token=token)
        with self.assertRaises(PolicyError) as conflict:
            self._prepare(self.record, expected_revision=1)
        self.assertEqual(conflict.exception.status, 412)
        with self.assertRaises(PolicyError) as conflict:
            self._prepare(self.record, community_revision=1)
        self.assertEqual(conflict.exception.status, 412)
        with self.assertRaises(ValueError):
            self._prepare(self.record, self.record)
        with self.assertRaises(ValueError):
            self._prepare(self.record, expected_revision=True)
        items = (BoardItem("issue", 1, "Synthetic open", "open", "2026-01-01T00:00:01Z"),
                 BoardItem("issue", 2, "Synthetic unapproved", "open", "2026-01-01T00:00:01Z"),
                 BoardItem("issue", 3, "Synthetic closed", "closed", "2026-01-01T00:00:01Z"),
                 BoardItem("pull", 3, "Same number different kind", "open", "2026-01-01T00:00:01Z"),
                 BoardItem("pull", 4, "Synthetic pull", "open", "2026-01-01T00:00:01Z"))
        self._publish_community(self.scope, items, {row.key for row in items if row.key != ("issue", 2)})
        current = self._record(self.scope)
        invalid = (self.record, self._record(self.scope, 2), self._record(self.scope, 3),
                   self._record(self.scope, 3, kind="pull"), self._record(self.scope, 4, kind="pull"),
                   replace(current, field_ids=("review",)), replace(current, field_ids=("invented",)))
        for record in invalid:
            with self.subTest(record=record), self.assertRaises(PolicyError) as invalid_target:
                self._prepare(record)
            self.assertEqual(invalid_target.exception.code, "invalid_suggestion_target")

    def test_same_number_isolation_and_unknown_or_denied_does_not_return_issue_data(self):
        self.assertEqual(self.save()["status"], 200)
        alternate = Scope("alpha", "second", "sample/project")
        shared = Scope("commons", "source", "sample/project")
        for other in (alternate, shared):
            prefix = f"/api/workspaces/{other.workspace_id}/responsibility/{other.connection_id}/{other.repository}"
            fields = [dict(id="other", name="Synthetic field", kind="developer", paths=["code"],
                           owner_ids=["admin"], backup=None)]
            self.assertEqual(self.save(config=fields, base=prefix, revision=1)["status"], 200)
            item = BoardItem("issue", 1, "Other synthetic issue", "open", "2026-01-01T00:00:00Z")
            self._publish_community(other, (item,), {item.key})
        batches = [self._prepare(self.record)]
        for other in (alternate, shared):
            batches.append(self._prepare(self._record(other, field_ids=("other",)), scope=other))
        self._attach(*batches)
        self.assertEqual(self._suggestion()["body"]["data"]["field_ids"], ["ui"])
        for other in (alternate, shared):
            prefix = f"/api/workspaces/{other.workspace_id}/responsibility/{other.connection_id}/{other.repository}"
            result = self._suggestion(base=prefix)
            self.assertEqual(result["body"]["data"]["scope"], other.as_dict())
            self.assertEqual(result["body"]["data"]["field_ids"], ["other"])
            with self.assertRaises(PolicyError):
                self._prepare(replace(self.record, field_ids=("other",)), scope=other)
        self._denied(self._suggestion(base=base.BASE.replace("/project", "/other")))
        self._denied(self._suggestion(base=base.BASE.replace("/alpha/", "/beta/")))
        self._denied(self._suggestion(token="test-second"))  # admin without scope grant
        self._denied(self._suggestion(token="test-outsider"))
        self._denied(self._suggestion(2))
        self._denied(self._suggestion(999))
        self.assertEqual(self._suggestion(token=None)["status"], 401)
        for query in ("", "number=0", "number=01", "number=9007199254740992",
                      "number=1&number=1", "number=1&actor=admin"):
            self.assertEqual(self.request("/suggestion", query=query)["status"], 400)

    def test_source_change_or_approval_replay_does_not_reuse_prepared_data(self):
        self.assertEqual(self.save()["status"], 200)
        self._attach(self._prepare(self.record))
        self.assertEqual(self._suggestion()["status"], 200)
        issue = self.cstate().snapshot.items[0]
        self._publish_community(self.scope, (issue,), {issue.key})
        self._denied(self._suggestion())  # byte-identical approval at a new revision
        self._attach(self._prepare(self.record))
        self.assertEqual(self._suggestion()["status"], 200)
        self._publish_community(self.scope, (issue,), set())
        self._denied(self._suggestion())
        replacement = replace(issue, title="Changed synthetic title", updated_at="2026-01-01T00:00:02Z")
        self._publish_community(self.scope, (replacement,), {replacement.key})
        self._denied(self._suggestion())

    def test_configuration_change_and_membership_revocation_retire_prepared_result(self):
        config = [dict(id="ui", name="Synthetic team", kind="developer",
                       paths=["ui"], owner_ids=["dev"], backup=None)]
        self.assertEqual(self.save(config=config)["status"], 200)
        self._attach(self._prepare(self.record))
        self.assertEqual(self._suggestion()["status"], 200)
        edited = [dict(config[0], name="Renamed synthetic team")]
        self.assertEqual(self.save(config=edited)["status"], 200)
        self._denied(self._suggestion())
        self._attach(self._prepare(self.record))
        self.store.set_membership("admin", "alpha", "reader", role="developer", state="active", expected_revision=1)
        self.store.set_membership("admin", "alpha", "reader", role="viewer", state="active", expected_revision=2)
        self._denied(self._suggestion())  # same reader and role, different audience revision
        self._attach(self._prepare(self.record))
        self.assertEqual(self._suggestion()["status"], 200)
        self.store.set_membership("second", "alpha", "admin", role="developer", state="active", expected_revision=1)
        self._denied(self._suggestion())  # field owner is still current, but publishing authority is not
        self.store.set_membership("second", "alpha", "admin", role="admin", state="active", expected_revision=2)
        self.assertEqual(community.call(self.community, "/approve", method="POST", token="test-admin",
            revision=self.cstate().revision, body={"fingerprints": [self.record.fingerprint]})["status"], 200)
        self._attach(self._prepare(self.record))
        self.assertEqual(self._suggestion()["status"], 200)
        self.store.set_membership("admin", "alpha", "reader", role="viewer", state="revoked", expected_revision=3)
        self._denied(self._suggestion())
        self._denied(self._suggestion(token="test-admin"))  # retired for the rest of the audience too

    def test_demoted_publisher_hides_suggestion_while_distinct_approval_stays_current(self):
        item = next(row for row in self.cstate().snapshot.items if row.key == ("issue", 1))
        state = self.cstate()
        replace_community_policy(self.store, replace(state.policy, approver_ids=("second",)),
                                 actor_id="admin", expected_revision=state.revision)
        self._publish_community(self.scope, (item,), {item.key}, approver="test-second")
        self.assertEqual(self.cstate().provenance["actor"], "second")
        # Neither the publishing admin nor the distinct Community approver
        # is a configured field owner, so this does not stale field references.
        config = [dict(id="ui", name="Synthetic team", kind="developer",
                       paths=["ui"], owner_ids=["dev"], backup=None)]
        self.assertEqual(self.save(config=config)["status"], 200)
        self._attach(self._prepare(self._record(self.scope)))
        self.assertEqual(self._suggestion()["status"], 200)
        parent = community.call(self.community, "/items")
        candidates = self.request("/candidates", query="view=all")
        self.assertEqual(parent["status"], 200)
        self.assertEqual([row["number"] for row in parent["body"]["data"]["items"]], [1])
        self.assertEqual(candidates["status"], 200)
        self.assertEqual([row["number"] for row in candidates["body"]["data"]["items"]], [1])

        self.store.set_membership("second", "alpha", "admin", role="developer", state="active", expected_revision=1)
        self.assertEqual(community.call(self.community, "/items"), parent)
        self.assertEqual(self.request("/candidates", query="view=all"), candidates)
        self._denied(self._suggestion())

    def test_batches_are_process_and_store_local_and_not_an_authority_token(self):
        self.assertEqual(self.save()["status"], 200)
        value = self._prepare(self.record)
        self._attach(value)
        self.assertEqual(self._suggestion()["status"], 200)
        self._attach(replace(value, records=(replace(self.record, field_ids=("core",)),)))
        self._denied(self._suggestion())
        self._attach(value)
        with self.assertRaises(ValueError):
            self._attach(value, value)
        with self.assertRaises(ValueError):
            self._attach(value, store=SQLiteWorkspaceService(self.path))
        with self.assertRaises(ValueError):
            self._attach({"scope": self.scope})
        with patch("packages.responsibility_api.suggestions.getpid", return_value=getpid() + 1000):
            self._denied(self._suggestion())
            with self.assertRaises(ValueError):
                self._attach(value)
