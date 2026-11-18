# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Pure V1 comparison: synthetic snapshot bytes, no clock, identity, store or TTY."""
from dataclasses import FrozenInstanceError, fields, replace
from hashlib import sha256
import json
from unittest import TestCase

from packages.pipeline_api import InvalidRecord, encode_snapshots
from packages.pipeline_sync.provider import ProviderError
from packages.pipeline_sync.replacement import describe_replacement
from packages.pipelines import PipelineScope


SCOPE = PipelineScope("example-space", "example-connection", "example-build")


def encoded(*ids, scope=SCOPE, at="2026-01-02T00:00:00Z"):
    return encode_snapshots(scope, [
        {**scope.as_dict(), "id": key, "observed_at": at, "status": "success",
         "stages": [{"name": "Fictional café", "jobs": []}]} for key in ids
    ])


class ReplacementDescriptionTests(TestCase):
    def test_shorter_subset_binds_actual_utf8_bytes_and_keeps_sorted_local_removals(self):
        before = encoded("removed-z", "kept", "removed-A")
        old = json.dumps(json.loads(before), ensure_ascii=False, indent=2)
        new = encoded("kept")
        result = describe_replacement(SCOPE, 7, old, new)
        self.assertEqual((result.scope, result.expected_revision, result.old_count, result.new_count,
                          result.removed_ids), (SCOPE, 7, 3, 1, ("removed-A", "removed-z")))
        self.assertIs(type(result.removed_ids), tuple)
        self.assertEqual(result.previous_utf8_sha256, sha256(old.encode("utf-8")).hexdigest())
        self.assertNotEqual(result.previous_utf8_sha256, sha256(before.encode("ascii")).hexdigest())
        self.assertEqual(result.candidate_utf8_sha256, sha256(new.encode("ascii")).hexdigest())
        self.assertEqual({f.name for f in fields(result)}, {
            "scope", "expected_revision", "old_count", "new_count", "removed_ids",
            "previous_utf8_sha256", "candidate_utf8_sha256"})
        with self.assertRaises(FrozenInstanceError):
            result.old_count = 0
        for text in (*SCOPE.as_dict().values(), *result.removed_ids, "Fictional",
                     result.previous_utf8_sha256, result.candidate_utf8_sha256):
            self.assertNotIn(text, repr(result))

    def test_all_old_ids_may_be_absent_from_a_nonempty_new_subset_but_empty_new_is_refused(self):
        new = encoded("new-only")
        for old, removals in [(encoded(), ()), (encoded("prior-b", "prior-a"), ("prior-a", "prior-b"))]:
            self.assertEqual(describe_replacement(SCOPE, 1, old, new).removed_ids, removals)
            with self.assertRaisesRegex(ProviderError, "^empty_observation$"):
                describe_replacement(SCOPE, 1, old, encoded())
        with self.assertRaisesRegex(ProviderError, "^invalid_review_candidate$"):
            describe_replacement(SCOPE, 1, encoded(), json.dumps(json.loads(new), ensure_ascii=False))

    def test_scope_must_match_both_envelopes_and_every_row(self):
        local = encoded("same-run")
        with self.assertRaises(InvalidRecord):
            describe_replacement(SCOPE.as_dict(), 1, local, local)
        for field in SCOPE.as_dict():
            foreign = encoded("same-run", scope=replace(SCOPE, **{field: "another-id"}))
            nested = json.loads(local)
            nested["runs"][0][field] = "another-id"
            for invalid in (foreign, json.dumps(nested, separators=(",", ":"))):
                for old, new in ((invalid, local), (local, invalid)):
                    with self.subTest(field=field, side="old" if old == invalid else "new"):
                        with self.assertRaises(InvalidRecord):
                            describe_replacement(SCOPE, 1, old, new)

    def test_revision_domain_and_freshness_are_separate_from_structural_description(self):
        old, new = encoded("kept"), encoded("kept", at="2026-01-01T00:00:00Z")
        # The retained row is older. Describing it is not permission to publish it.
        for valid in (1, 2**53-1):
            result = describe_replacement(SCOPE, valid, old, new)
            self.assertEqual((result.expected_revision, result.removed_ids), (valid, ()))
        for invalid in (True, False, 1.0, "1", 0, -1, 2**53):
            with self.subTest(revision=invalid), self.assertRaises(ValueError):
                describe_replacement(SCOPE, invalid, old, new)

    def test_invalid_or_oversized_input_on_either_side_fails_without_echo(self):
        local = encoded("same-run")
        # The non-ASCII stage name makes this an actual byte bound, not a
        # character bound. JSON permits all of the trailing spaces on both sides.
        raw = json.dumps(json.loads(local), ensure_ascii=False)
        self.assertGreater(len(raw.encode("utf-8")), len(raw))
        at_limit = raw + " " * (10000 - len(raw.encode("utf-8")))
        over_limit = at_limit + " "
        self.assertEqual((len(at_limit.encode("utf-8")), len(over_limit.encode("utf-8"))), (10000, 10001))
        self.assertLessEqual(len(over_limit), 10000)
        self.assertEqual(json.loads(over_limit), json.loads(local))
        self.assertEqual(describe_replacement(SCOPE, 1, at_limit, local).previous_utf8_sha256,
                         sha256(at_limit.encode("utf-8")).hexdigest())
        with self.assertRaises(InvalidRecord):
            describe_replacement(SCOPE, 1, over_limit, local)
        marker = "fictional-excluded-source-value"
        unexpected = json.loads(local)
        unexpected["runs"][0]["private_note"] = marker
        for invalid in (json.dumps(unexpected), " " * 10001, None):
            for old, new in ((invalid, local), (local, invalid)):
                with self.assertRaises(InvalidRecord) as failure:
                    describe_replacement(SCOPE, 1, old, new)
                self.assertNotIn(marker, str(failure.exception))
