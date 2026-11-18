# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Private, synthetic decision framing; the real TTY path is tested separately."""
from dataclasses import replace
import hashlib
import json
from unittest import TestCase
from unittest.mock import patch

from packages.pipeline_api import encode_snapshots
from packages.pipeline_sync import ProviderError, review
from packages.pipeline_sync.tests._review_fixture import AUDIENCE, NOW, SCOPE, ReviewedFixture


class ReviewDecisionTests(ReviewedFixture, TestCase):
    def test_digest_commits_to_nonce_full_old_and_new_bytes_audience_reviewer_revision_and_source(self):
        conn = self.args["connection"]
        policy = self.args["review_policy"]
        old = encode_snapshots(SCOPE, [])
        row = {**SCOPE.as_dict(), "id":"a", "stages":[], "status":"failure", "observed_at":NOW.isoformat()}
        new = encode_snapshots(SCOPE, [row])
        nonce = "50"*32
        sha = lambda text: hashlib.sha256(text.encode("utf-8")).hexdigest()
        with patch.object(review.secrets, "token_hex", return_value=nonce) as issue:
            _, digest = review._review(conn, policy, 2, old, new)
            issue.assert_called_once_with(32)
            # Independently frame the contract, including the raw baseline
            # bytes (two semantically equal JSON records need not be identical).
            binding = {"projection":"pipeline-snapshot-content-and-audience-review/v1", "nonce":nonce,
                       "scope":SCOPE.as_dict(), "audience":AUDIENCE, "expected_revision":2,
                       "source":{"origin":conn.origin, "run_limit":5, "timeout":10, "max_response_bytes":262144},
                       "reviewer_pin":policy.reviewer_pin, "reviewer_epoch":1, "membership_revision":1,
                       "previous_utf8_sha256":sha(old), "candidate_utf8_sha256":sha(new)}
            expected = sha(json.dumps(binding, ensure_ascii=True, separators=(",",":"), sort_keys=True))
            self.assertTrue(expected==digest, "digest omitted or changed review binding")
            # Hold nonce fixed so randomness alone cannot make the tests pass.
            for before, after, version in [(old+" ",new,2), (old,new,3),
                                            (old,encode_snapshots(SCOPE,[dict(row,status="success")]),2)]:
                self.assertTrue(review._review(conn,policy,version,before,after)[1]!=digest)
            other = replace(policy, connection=replace(conn, run_limit=4))
            self.assertTrue(review._review(other.connection,other,2,old,new)[1]!=digest)
            self.assertTrue(review._review(conn,replace(policy,membership_revision=2),2,old,new)[1]!=digest)

    def test_noncanonical_candidate_bytes_never_reach_terminal_even_when_the_data_would_decode(self):
        old = encode_snapshots(SCOPE, [])
        row = {**SCOPE.as_dict(), "id":"a", "stages":None, "status":"unknown", "observed_at":NOW.isoformat()}
        encoded = encode_snapshots(SCOPE, [row])
        with patch.object(review,"_terminal") as screen:
            with self.assertRaisesRegex(ProviderError,"^invalid_review_candidate$"):
                review.confirm_replacement(self.args["connection"],self.args["review_policy"],1,old,
                                           encoded+" ",verifier=lambda: self.fail("candidate shown"))
            self.assertEqual(screen.call_count,0,"terminal opened for noncanonical input")
