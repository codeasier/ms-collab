# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Fictional reviewed-refresh gate; no network or inherited credentials."""
from datetime import timedelta
from pathlib import Path
import tempfile

from packages.identity import Credential, Provider
from packages.pipeline_api import encode_snapshots, resource_id
from packages.pipeline_sync import BoundCredential, Connection, PipelineReviewPolicy, PipelineSynchronizer
from packages.pipeline_sync.tests.test_sync import Fake, ISSUER, NOW, ORIGIN, SCOPE, TOKEN, Verifier, initialize
from packages.workspaces.authority import principal_pin


AUDIENCE = "current-and-future-workspace-pipeline-readers/v1"
LABEL = 'Fictional "café" \\ checks'
OLD_IDS = ("old-a", "old-b")
USER_TOKEN = "synthetic-local-operator-token-for-tests-only"


def providers():
    class FixtureVerifier(Verifier):
        def verify(self, credential):
            if credential.value == USER_TOKEN:
                credential = Credential("admin")
            return super().verify(credential)
    return (Provider("test", ISSUER, "pipeline-test", FixtureVerifier()),)


def policy_for(store, connection):
    # Trusted fixture provisioning, deliberately independent of the caller's
    # bearer, just like the read-only connection binding. Never auto-enroll a
    # reviewer extracted from an incoming request.
    def operator(policy):
        return PipelineReviewPolicy(connection=connection, reviewer_pin=principal_pin(policy, "admin"),
                                    reviewer_epoch=policy._principals["admin"].authority_epoch,
                                    membership_revision=policy._memberships[("alpha", "admin")].revision,
                                    audience=AUDIENCE)
    return store._transaction(operator, write=False)


def setup_gate(store, *, policy=None):
    connection = Connection(SCOPE, ORIGIN)
    fake = Fake()
    fake.detail["stages"][0]["jobs"][0]["name"] = LABEL
    fake.detail["private_debug"] = "SYNTHETIC-EXCLUDED-FIELD"
    sync = PipelineSynchronizer(store=store, providers=providers(), provider_key="test",
                                clock=lambda: NOW, transport=fake)
    args = dict(user_credential=Credential(USER_TOKEN), connection=connection,
                upstream_credential=BoundCredential(SCOPE, ORIGIN, TOKEN),
                expected_revision=1, review_policy=policy if policy is not None else policy_for(store, connection))
    return sync, args, fake


class ReviewedFixture:
    def setUp(self):
        directory = tempfile.TemporaryDirectory()
        self.addCleanup(directory.cleanup)
        self.path = Path(directory.name) / "synthetic.sqlite"
        self.store = initialize(self.path, Connection(SCOPE, ORIGIN))
        self.sync, self.args, self.fake = setup_gate(self.store)

    def seed_old(self):
        rows = [{**SCOPE.as_dict(), "id": ident, "observed_at": (NOW-timedelta(days=1)).isoformat(),
                 "status": "success", "stages": None} for ident in OLD_IDS]
        record = self.store.update_resource("admin", "alpha", "pipelines", resource_id(SCOPE),
                                            text=encode_snapshots(SCOPE, rows), expected_revision=1)
        self.args["expected_revision"] = record.revision

    def footprint(self):
        row = self.store.read_resource("reader", "alpha", "pipelines", resource_id(SCOPE))
        updates = tuple(e for e in self.store.audit_events("admin", "alpha") if e.action == "resource.update")
        return row, updates
