# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Immutable I1 declarations; never regenerate from latest module schemas.

Materialized by static AST/string expansion of the pinned baseline. SQLite
normalization/digest equivalence is a remote-CI gate, not a local import claim.
Changing these historical declarations requires explicit compatibility review.
"""

SOURCE_COMMIT = '1d19ff6b1ae62ca5e17cc60f47f910e0e18a39a5'
RAW_DEFINITION_SHA256 = '3449950d7674518306c2ec8e9ef4e02b2d02c6567d55d9f7bcff7b92e7cd1616'
# Hash encoding: raw format/groups/constants payload, UTF-8 JSON, ensure_ascii=False,
# sort_keys=True and separators=(comma,colon), no trailing newline.

_APPLICATION_ID = 1297302359
_SCHEMA_VERSION = 3
_APP = 1297304659
_VERSION = 2

WORKSPACE = (
    # WORKSPACE:1 packages/workspaces/sqlite.py:46 (_SCHEMA_V2); SHA256 1be78174525472c773a5bf57644617e1ef913a6a341adf149dec9e92465327a4
    "CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK (id=1), epoch INTEGER NOT NULL CHECK (epoch>=1), realm TEXT NOT NULL DEFAULT 'uninitialized') STRICT",
    # WORKSPACE:2 packages/workspaces/sqlite.py:47 (_SCHEMA_V2); SHA256 6e235265f9e0a4404d89bd1861fabcd1dc0f976a8c572c763a9f3ff92323c5b1
    'CREATE TABLE principals (id TEXT PRIMARY KEY, issuer TEXT NOT NULL, subject TEXT NOT NULL, active INTEGER NOT NULL CHECK (active IN (0,1)), generation INTEGER NOT NULL DEFAULT 1 CHECK(generation>=1 AND generation<=9007199254740991), authority_epoch INTEGER NOT NULL DEFAULT 1 CHECK(authority_epoch>=1 AND authority_epoch<=9007199254740991), UNIQUE(issuer,subject)) STRICT',
    # WORKSPACE:3 packages/workspaces/sqlite.py:25 (_SCHEMA_V1); SHA256 69953cd1836b70b58a88c26663d1f26ebda764437517c7f3b135fb2a4689a591
    'CREATE TABLE operators (principal_id TEXT PRIMARY KEY REFERENCES principals(id)) STRICT',
    # WORKSPACE:4 packages/workspaces/sqlite.py:26 (_SCHEMA_V1); SHA256 ad2d6fd6dbf01239494a1d9a4c299847209597721ff1547848f37ce6f0b786b2
    "CREATE TABLE workspaces (id TEXT PRIMARY KEY, name TEXT NOT NULL, kind TEXT NOT NULL CHECK (kind IN ('product','shared')), state TEXT NOT NULL CHECK (state IN ('active','suspended','archived')), revision INTEGER NOT NULL CHECK (revision>=1)) STRICT",
    # WORKSPACE:5 packages/workspaces/sqlite.py:27 (_SCHEMA_V1); SHA256 d4c95987c860c85595f41106613de0b603b1990ec18cab4c2060c15aa27d8af7
    "CREATE TABLE memberships (workspace_id TEXT NOT NULL REFERENCES workspaces(id), principal_id TEXT NOT NULL REFERENCES principals(id), role TEXT NOT NULL CHECK (role IN ('viewer','developer','admin')), state TEXT NOT NULL CHECK (state IN ('active','revoked')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,principal_id)) STRICT",
    # WORKSPACE:6 packages/workspaces/sqlite.py:28 (_SCHEMA_V1); SHA256 dfbc06702a0c6e76da4c163627b67ee9abfb25c9472a8933192f4c179dce1099
    "CREATE TABLE modules (workspace_id TEXT NOT NULL REFERENCES workspaces(id), key TEXT NOT NULL, can_read INTEGER NOT NULL CHECK (can_read IN (0,1)), can_write INTEGER NOT NULL CHECK (can_write IN (0,1)), state TEXT NOT NULL CHECK (state IN ('enabled','disabling','disabled')), revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,key)) STRICT",
    # WORKSPACE:7 packages/workspaces/sqlite.py:29 (_SCHEMA_V1); SHA256 74b0563a6ab92283719dbaa5d7d807c3c4c9058baf22a8fe394543970deac48d
    'CREATE TABLE resources (workspace_id TEXT NOT NULL, module_key TEXT NOT NULL, id TEXT NOT NULL, text TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1), PRIMARY KEY(workspace_id,module_key,id), FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT',
    # WORKSPACE:8 packages/workspaces/sqlite.py:30 (_SCHEMA_V1); SHA256 50a7ee43e82c0bd8a1b926d4c6f4190dfdd04c451391853f52dae4b9871c32f1
    'CREATE TABLE audit (sequence INTEGER PRIMARY KEY CHECK (sequence>=1), actor_id TEXT NOT NULL REFERENCES principals(id), workspace_id TEXT NOT NULL REFERENCES workspaces(id), action TEXT NOT NULL, object_id TEXT NOT NULL, revision INTEGER NOT NULL CHECK (revision>=1)) STRICT',
    # WORKSPACE:9 packages/workspaces/sqlite.py:44 (_MIGRATIONS); SHA256 f89790279f160a0ca1b9d175158cbfc063bf81ace9fd0730cfb4664023744a9b
    'CREATE TABLE authority_migrations (id INTEGER PRIMARY KEY CHECK(id=1), from_version INTEGER NOT NULL CHECK(from_version IN (0,1)), to_version INTEGER NOT NULL CHECK(to_version=2), principal_count INTEGER NOT NULL CHECK(principal_count>=0), realm TEXT NOT NULL) STRICT',
    # WORKSPACE:10 packages/workspaces/sqlite.py:40 (_PRINCIPAL_GUARDS); SHA256 27369f65327f81133bc0f70705d0fa8e4f14114261413d90acfc3d4b42a54e9a
    "CREATE TRIGGER principal_authority_closed BEFORE UPDATE ON principals WHEN NEW.id!=OLD.id OR NEW.issuer!=OLD.issuer OR NEW.subject!=OLD.subject OR NEW.active!=OLD.active OR NEW.generation!=OLD.generation OR NEW.authority_epoch!=OLD.authority_epoch BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:11 packages/workspaces/sqlite.py:41 (_PRINCIPAL_GUARDS); SHA256 523c84b7cdc7263c0e6035bb6b0ee1cbf3239056d1edf0c09451278acfa3e3bb
    "CREATE TRIGGER principal_delete_closed BEFORE DELETE ON principals BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:12 packages/workspaces/sqlite.py:42 (_PRINCIPAL_GUARDS); SHA256 179c96cefd5b5b0c7286007f8c17f7e929594ef01a4ad40dc7b17dc6a96adc15
    "CREATE TRIGGER principal_replace_closed BEFORE INSERT ON principals WHEN EXISTS(SELECT 1 FROM authority_migrations) OR EXISTS(SELECT 1 FROM principals WHERE id=NEW.id OR (issuer=NEW.issuer AND subject=NEW.subject)) BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:13 packages/workspaces/sqlite.py:49 (_SCHEMA_V2); SHA256 535dca4050bce2ba72aac2bf72d9eec6cb19bc7e70f10a586b3d85a1ad0c7f38
    "CREATE TRIGGER policy_authority_monotonic BEFORE UPDATE ON metadata WHEN NEW.id!=OLD.id OR NEW.realm!=OLD.realm OR NEW.epoch<OLD.epoch OR NEW.epoch>9007199254740991 BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    # WORKSPACE:14 packages/workspaces/sqlite.py:50 (_SCHEMA_V2); SHA256 a53720d83b6a07a057f59e9acabd5b29a2ab0b3cae5305789f4206f49532cf46
    "CREATE TRIGGER policy_metadata_delete_closed BEFORE DELETE ON metadata BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    # WORKSPACE:15 packages/workspaces/sqlite.py:51 (_SCHEMA_V2); SHA256 06d8f6b1a54fd769c2e5d2d4216ad75ea057c646c4bc038412eb4b97c00bf8f2
    "CREATE TRIGGER policy_metadata_replace_closed BEFORE INSERT ON metadata WHEN EXISTS(SELECT 1 FROM metadata) BEGIN SELECT RAISE(ABORT,'invalid_authority_epoch'); END",
    # WORKSPACE:16 packages/workspaces/sqlite.py:52 (_SCHEMA_V2); SHA256 ec8907ab52781db1a2984c1995b38ae604830f3c2907cdfe47f2e619136037a5
    "CREATE TRIGGER authority_marker_update_closed BEFORE UPDATE ON authority_migrations BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:17 packages/workspaces/sqlite.py:53 (_SCHEMA_V2); SHA256 5273c6e7a4b7f85367319fec9b3fdca0606264c415c70e36577d7817e662b36c
    "CREATE TRIGGER authority_marker_delete_closed BEFORE DELETE ON authority_migrations BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:18 packages/workspaces/sqlite.py:54 (_SCHEMA_V2); SHA256 413563bea61665ff9ed77e11491c299d7cd40e9e28ccb417d33cb80c9dcf4f26
    "CREATE TRIGGER authority_marker_replace_closed BEFORE INSERT ON authority_migrations WHEN EXISTS(SELECT 1 FROM authority_migrations) BEGIN SELECT RAISE(ABORT,'identity_transition_unavailable'); END",
    # WORKSPACE:19 packages/workspaces/sqlite.py:65 (_GRANT_SCHEMA); SHA256 376b11e2911362a762ffccb860ec1966863dbc2f1909e4aec87e8fbccdd6f342
    'CREATE TABLE membership_authority (workspace_id TEXT NOT NULL, principal_id TEXT NOT NULL, realm TEXT, issuer TEXT, subject TEXT, generation INTEGER CHECK(generation IS NULL OR (generation>=1 AND generation<=9007199254740991)), PRIMARY KEY(workspace_id,principal_id), FOREIGN KEY(workspace_id,principal_id) REFERENCES memberships(workspace_id,principal_id), CHECK((realm IS NULL AND issuer IS NULL AND subject IS NULL AND generation IS NULL) OR (realm IS NOT NULL AND issuer IS NOT NULL AND subject IS NOT NULL AND generation IS NOT NULL))) STRICT',
    # WORKSPACE:20 packages/workspaces/sqlite.py:69 (_GRANT_SCHEMA); SHA256 58f1bce2303242a5cbc4a620b58d64f4a4d046d2247cf5ff8f8d2a9982104aaf
    'CREATE TABLE operator_authority (principal_id TEXT PRIMARY KEY REFERENCES operators(principal_id), realm TEXT, issuer TEXT, subject TEXT, generation INTEGER CHECK(generation IS NULL OR (generation>=1 AND generation<=9007199254740991)), CHECK((realm IS NULL AND issuer IS NULL AND subject IS NULL AND generation IS NULL) OR (realm IS NOT NULL AND issuer IS NOT NULL AND subject IS NOT NULL AND generation IS NOT NULL))) STRICT',
)

LOGIN = (
    # LOGIN:1 packages/login_state/core.py:30 (_SCHEMA_V1); SHA256 d928c2873f0e71a66072a9841af6e8803c375d21ebabca2bb970bc5dc0795ec7
    'CREATE TABLE metadata (id INTEGER PRIMARY KEY CHECK(id=1), binding TEXT NOT NULL, clock INTEGER NOT NULL) STRICT',
    # LOGIN:2 packages/login_state/core.py:39 (_SCHEMA); SHA256 8e68d6478eeec8423818c6ed25132b8de5835b121c75639250afe67a577cd93c
    'CREATE TABLE attempts (state TEXT PRIMARY KEY, browser TEXT NOT NULL, verifier TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, policy_realm TEXT, policy_epoch INTEGER) STRICT',
    # LOGIN:3 packages/login_state/core.py:39 (_SCHEMA); SHA256 ae4cb50bac3d00eb98f2fa53d705a886d4026bbb5dff90b6ec27b4855f5ceb57
    'CREATE TABLE sessions (token TEXT PRIMARY KEY, csrf TEXT NOT NULL, subject TEXT NOT NULL, created INTEGER NOT NULL, expires INTEGER NOT NULL, principal_id TEXT, principal_generation INTEGER, policy_realm TEXT) STRICT',
)

ADMIN = (
    # ADMIN:1 packages/workspace_admin_api/requests.py:19 (_CREATE); SHA256 b1b7b2c73399e32cd28abca3e212783fb80a9eb38ff93ef7dfc67c70f5f08de7
    "CREATE TABLE workspace_admin_requests_v2 (\n    actor TEXT NOT NULL REFERENCES principals(id),\n    action TEXT NOT NULL CHECK(action IN ('workspace.create','module.install')),\n    scope TEXT NOT NULL, request_key TEXT NOT NULL, digest TEXT NOT NULL,\n    result_id TEXT NOT NULL, authority TEXT,\n    PRIMARY KEY(actor,action,scope,request_key)) STRICT",
)

INVITES = (
    # INVITES:1 packages/workspace_invitations/store.py:12 (_CREATE); SHA256 3059b16d6f051a144effc88e61e724310795f86f0ed85c1f028e13090bad2f2b
    "CREATE TABLE workspace_invitations_v1 (\n    id TEXT PRIMARY KEY,\n    workspace_id TEXT NOT NULL REFERENCES workspaces(id),\n    inviter TEXT NOT NULL REFERENCES principals(id),\n    principal_id TEXT NOT NULL REFERENCES principals(id),\n    binding TEXT NOT NULL, inviter_binding TEXT NOT NULL,\n    role TEXT NOT NULL CHECK(role IN ('viewer','developer')),\n    state TEXT NOT NULL CHECK(state IN ('pending','accepted','revoked','declined')),\n    created INTEGER NOT NULL, expires INTEGER NOT NULL CHECK(expires>created),\n    revision INTEGER NOT NULL CHECK(revision>=1),\n    workspace_revision INTEGER NOT NULL CHECK(workspace_revision>=1),\n    inviter_revision INTEGER NOT NULL CHECK(inviter_revision>=1),\n    member_revision INTEGER NOT NULL CHECK(member_revision>=0),\n    request_key TEXT NOT NULL,\n    UNIQUE(workspace_id,inviter,request_key)) STRICT",
)

COMMUNITY = (
    # COMMUNITY:1 packages/community_api/records.py:17 (_SCHEMA); SHA256 b087efb9f98354706a5f31599697b5e1683d7e2c66b59d078f6ecc153616e175
    "CREATE TABLE community_connections (\n workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='community'),\n connection_id TEXT NOT NULL, repository TEXT NOT NULL, config TEXT NOT NULL,\n snapshot TEXT, approvals TEXT NOT NULL, revision INTEGER NOT NULL CHECK(revision>=1),\n PRIMARY KEY(workspace_id,connection_id,repository),\n FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)\n) STRICT",
)

ARTIFACTS = (
    # ARTIFACTS:1 packages/artifact_sync/records.py:17 (_CREATE); SHA256 4198136b2bcd1573c18ea3088679cd0570e70da4f24d3668affad8a379279aa2
    'CREATE TABLE artifact_indexes_v1 (\n workspace TEXT NOT NULL REFERENCES workspaces(id), connection TEXT NOT NULL, bucket TEXT NOT NULL,\n revision INTEGER NOT NULL CHECK(revision>=1), policy TEXT NOT NULL,\n candidate TEXT, published TEXT, published_revision INTEGER,\n PRIMARY KEY(workspace,connection,bucket),\n CHECK((published IS NULL AND published_revision IS NULL) OR\n       (published IS NOT NULL AND published_revision>=1 AND published_revision<=revision))) STRICT',
)

ARTIFACT_EVENTS = (
    # ARTIFACT_EVENTS:1 packages/artifact_sync/records.py:24 (_EVENT_CREATE); SHA256 8efaabb9cef5a445e3fc7954ff0b6865904b45b971009c647a90dbee86de751b
    "CREATE TABLE artifact_index_events_v1 (\n sequence INTEGER PRIMARY KEY, actor TEXT NOT NULL REFERENCES principals(id),\n workspace TEXT NOT NULL, connection TEXT NOT NULL, bucket TEXT NOT NULL,\n action TEXT NOT NULL CHECK(action IN ('refresh','approve')), revision INTEGER NOT NULL CHECK(revision>=1),\n digest TEXT NOT NULL, FOREIGN KEY(workspace,connection,bucket)\n REFERENCES artifact_indexes_v1(workspace,connection,bucket)) STRICT",
)

HISTORY = (
    # HISTORY:1 packages/assistant_history/store.py:12 (_TABLES); SHA256 35faab7521b707be59d8ccd3c9ce37ec2d1a9f3dfbc3f73e8fbd108ad5695bb5
    "CREATE TABLE assistant_conversations (\n workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='assistant'),\n id TEXT NOT NULL, owner_id TEXT NOT NULL, title TEXT NOT NULL,\n state TEXT NOT NULL CHECK(state IN ('active','archived')),\n revision INTEGER NOT NULL CHECK(revision>=1), created_at TEXT NOT NULL,\n updated_at TEXT NOT NULL, fence TEXT NOT NULL,\n event_count INTEGER NOT NULL CHECK(event_count>=0 AND event_count<=2000),\n event_bytes INTEGER NOT NULL CHECK(event_bytes>=0 AND event_bytes<=1048576),\n PRIMARY KEY(workspace_id,id),\n FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key),\n FOREIGN KEY(owner_id) REFERENCES principals(id)) STRICT",
    # HISTORY:2 packages/assistant_history/store.py:23 (_TABLES); SHA256 27881737236e016650f8f33c4a1802232ee923a2293ddad0cb9ba6d823d86825
    'CREATE TABLE assistant_events (\n workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,\n sequence INTEGER NOT NULL CHECK(sequence>=1), event TEXT NOT NULL,\n PRIMARY KEY(workspace_id,conversation_id,sequence),\n FOREIGN KEY(workspace_id,conversation_id) REFERENCES assistant_conversations(workspace_id,id)) STRICT',
)

RESPONSIBILITY = (
    # RESPONSIBILITY:1 packages/responsibility_api/records.py:15 (_SCHEMA); SHA256 8f02f1215ea2c6d949cf2009e596155eda1bfd935ba7cc4e02d491b03a2a70d8
    'CREATE TABLE responsibility_connections_v1 (\n workspace_id TEXT NOT NULL,connection_id TEXT NOT NULL,repository TEXT NOT NULL,\n policy TEXT NOT NULL,fields TEXT NOT NULL,config_version INTEGER NOT NULL CHECK(config_version>=1),\n evidence TEXT NOT NULL,community_revision INTEGER NOT NULL CHECK(community_revision>=0),\n observed_at TEXT,revision INTEGER NOT NULL CHECK(revision>=1),\n PRIMARY KEY(workspace_id,connection_id,repository),\n FOREIGN KEY(workspace_id,connection_id,repository) REFERENCES community_connections(workspace_id,connection_id,repository)\n) STRICT',
)

GUI = (
    # GUI:1 packages/gui_smoke/store.py:15 (_SCHEMA); SHA256 627013e423489f1ebcfccd6a0ec9c3a862deacc25647c23558538c440ab1326a
    "CREATE TABLE gui_smoke_snapshots (\n workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='gui_smoke'),\n connection_id TEXT NOT NULL, repository_id TEXT NOT NULL,\n revision INTEGER NOT NULL CHECK(revision>=1), record TEXT NOT NULL,\n PRIMARY KEY(workspace_id,connection_id,repository_id),\n FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT",
)

GUI_GRANTS = (
    # GUI_GRANTS:1 packages/gui_smoke/grants.py:11 (_SCHEMA); SHA256 69dbc49b30d66b2d2010971a2ccb550cde0d37bff9c3e1ece81bb4d72c1a5f18
    "CREATE TABLE gui_smoke_grants (\n workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='gui_smoke'),\n connection_id TEXT NOT NULL, repository_id TEXT NOT NULL,\n grant_revision INTEGER NOT NULL CHECK(grant_revision>=1 AND grant_revision<=9007199254740991),\n snapshot_revision INTEGER NOT NULL CHECK(snapshot_revision>=0 AND snapshot_revision<=9007199254740991),\n record TEXT NOT NULL, PRIMARY KEY(workspace_id,connection_id,repository_id),\n FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key)) STRICT",
)

ASSETS = (
    # ASSETS:1 packages/research_assets/store.py:8 (_SCHEMA); SHA256 fa1b50d9d98f126ee1ca640c4bb506839dd756f0c6cbba62c4aea17811a5e012
    'CREATE TABLE research_catalog (\n            workspace_id TEXT NOT NULL, catalog_id TEXT NOT NULL, binding TEXT NOT NULL,\n            revision INTEGER NOT NULL CHECK(revision>=1), evidence TEXT NOT NULL, available INTEGER NOT NULL CHECK(available IN (0,1)),\n            PRIMARY KEY(workspace_id,catalog_id), FOREIGN KEY(workspace_id) REFERENCES workspaces(id)) STRICT',
    # ASSETS:2 packages/research_assets/store.py:12 (_SCHEMA); SHA256 9680b4240be518a2db4f45b706c2bc4bf372d7a7df0470edaaeee5c512d9c4e7
    'CREATE TABLE research_asset (\n            workspace_id TEXT NOT NULL, catalog_id TEXT NOT NULL, id TEXT NOT NULL,\n            type_id TEXT NOT NULL, entity_id TEXT NOT NULL, title TEXT NOT NULL,\n            search_text TEXT NOT NULL, summary TEXT NOT NULL, summary_digest TEXT NOT NULL, detail TEXT NOT NULL,\n            PRIMARY KEY(workspace_id,catalog_id,id),\n            FOREIGN KEY(workspace_id,catalog_id) REFERENCES research_catalog(workspace_id,catalog_id)) STRICT',
    # ASSETS:3 packages/research_assets/store.py:18 (_SCHEMA); SHA256 4f07a5cd41520733dce2ace013950aa2b0c0f7106c102901ff421969b0fc0b38
    'CREATE INDEX research_asset_query ON research_asset(workspace_id,catalog_id,type_id,entity_id,title,id)',
)

GROUPS = (
    ('WORKSPACE', WORKSPACE),
    ('LOGIN', LOGIN),
    ('ADMIN', ADMIN),
    ('INVITES', INVITES),
    ('COMMUNITY', COMMUNITY),
    ('ARTIFACTS', ARTIFACTS),
    ('ARTIFACT_EVENTS', ARTIFACT_EVENTS),
    ('HISTORY', HISTORY),
    ('RESPONSIBILITY', RESPONSIBILITY),
    ('GUI', GUI),
    ('GUI_GRANTS', GUI_GRANTS),
    ('ASSETS', ASSETS),
)

MODULE_SCHEMA = (*ADMIN, *INVITES, *COMMUNITY, *ARTIFACTS, *ARTIFACT_EVENTS, *HISTORY,
                 *RESPONSIBILITY, *GUI, *GUI_GRANTS, *ASSETS)
POLICY_SCHEMA = (*WORKSPACE, *MODULE_SCHEMA)
