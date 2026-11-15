# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Dedicated owner-bound records inside the policy store's SAME transaction."""
from datetime import datetime, timezone
import hashlib

from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from .model import DisplayEvent, MAX_BYTES, MAX_EVENTS, MAX_REVISION, decode, encode, identifier, positive, text, timestamp

_TABLES = {
    "assistant_conversations": """CREATE TABLE assistant_conversations (
 workspace_id TEXT NOT NULL, module_key TEXT NOT NULL CHECK(module_key='assistant'),
 id TEXT NOT NULL, owner_id TEXT NOT NULL, title TEXT NOT NULL,
 state TEXT NOT NULL CHECK(state IN ('active','archived')),
 revision INTEGER NOT NULL CHECK(revision>=1), created_at TEXT NOT NULL,
 updated_at TEXT NOT NULL, fence TEXT NOT NULL,
 event_count INTEGER NOT NULL CHECK(event_count>=0 AND event_count<=2000),
 event_bytes INTEGER NOT NULL CHECK(event_bytes>=0 AND event_bytes<=1048576),
 PRIMARY KEY(workspace_id,id),
 FOREIGN KEY(workspace_id,module_key) REFERENCES modules(workspace_id,key),
 FOREIGN KEY(owner_id) REFERENCES principals(id)) STRICT""",
    "assistant_events": """CREATE TABLE assistant_events (
 workspace_id TEXT NOT NULL, conversation_id TEXT NOT NULL,
 sequence INTEGER NOT NULL CHECK(sequence>=1), event TEXT NOT NULL,
 PRIMARY KEY(workspace_id,conversation_id,sequence),
 FOREIGN KEY(workspace_id,conversation_id) REFERENCES assistant_conversations(workspace_id,id)) STRICT""",
}


def validate_schema(connection):
    for name, expected in _TABLES.items():
        row = connection.execute("SELECT sql FROM sqlite_master WHERE type='table' AND name=?", (name,)).fetchone()
        if row is None or type(row[0]) is not str or " ".join(row[0].split()) != " ".join(expected.split()):
            raise StorageError("invalid_assistant_schema")


def initialize(store):
    """Explicit trusted setup only. No implicit data migration or imported history."""
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("configured_store_required")
    def setup(policy, connection):
        for name, schema in _TABLES.items():
            if connection.execute("SELECT 1 FROM sqlite_master WHERE name=?", (name,)).fetchone() is None:
                connection.execute(schema)
        validate_schema(connection)
    store._transaction(setup, write=True, with_connection=True)


def _now(clock):
    value = clock()
    if not isinstance(value, datetime) or value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("aware_clock_required")
    return value.astimezone(timezone.utc).isoformat()


def authority(policy, owner, workspace, modules, action="read"):
    """A fence detects scope/binding changes; it never replaces current checks."""
    if type(modules) is not list or not 1 <= len(modules) <= 16 or modules != sorted(set(modules)) or "assistant" not in modules:
        raise ValueError("invalid_dependencies")
    for module in modules:
        identifier(module)
        policy.authorize(owner, workspace, module, "read")
        if module == "assistant" and action == "write":policy.authorize(owner, workspace, module, "write")
    principal = policy._principals[owner]
    return {"issuer": principal.issuer, "subject": principal.subject,
            "workspace_revision": policy._workspaces[workspace].revision,
            "membership_revision": policy._memberships[(workspace,owner)].revision,
            "modules": {key:policy._modules[(workspace,key)].revision for key in modules}}


def _row(connection, workspace, conversation, owner):
    row = connection.execute("SELECT id,title,state,revision,created_at,updated_at,fence,event_count,event_bytes FROM assistant_conversations WHERE workspace_id=? AND id=? AND owner_id=?", (workspace,conversation,owner)).fetchone()
    if row is None:
        raise PolicyError(404, "not_found")
    try:
        identifier(row[0]); text(row[1],limit=800); positive(row[3])
        if row[2] not in {"active","archived"} or timestamp(row[5]) < timestamp(row[4]):
            raise ValueError()
        fence=decode(row[6])
        if (type(fence) is not dict or set(fence) != {"issuer","subject","workspace_revision","membership_revision","modules"}
                or type(fence["modules"]) is not dict or not 1 <= len(fence["modules"]) <= 16):
            raise ValueError()
        for binding in (fence["issuer"],fence["subject"]):
            if type(binding) is not str or not 1 <= len(binding) <= 200 or any(c.isspace() for c in binding):raise ValueError()
        positive(fence["workspace_revision"]);positive(fence["membership_revision"])
        for key,value in fence["modules"].items():identifier(key);positive(value)
        if "assistant" not in fence["modules"] or type(row[7]) is not int or not 0 <= row[7] <= MAX_EVENTS or type(row[8]) is not int or not 0 <= row[8] <= MAX_BYTES:
            raise ValueError()
        count, total, low, high = connection.execute("SELECT COUNT(*),COALESCE(SUM(length(event)),0),MIN(sequence),MAX(sequence) FROM assistant_events WHERE workspace_id=? AND conversation_id=?", (workspace,conversation)).fetchone()
        if (count,total) != (row[7],row[8]) or (count and (low != 1 or high != count)):
            raise ValueError()
    except (ValueError, TypeError, KeyError, UnicodeError, RecursionError, OverflowError):
        raise StorageError("invalid_assistant_record") from None
    return {"workspace_id":workspace,"id":row[0],"title":row[1],"state":row[2],"revision":row[3],
            "created_at":row[4],"updated_at":row[5],"event_count":row[7],"event_bytes":row[8],"fence":fence}


def authorized_row(policy, connection, owner, workspace, conversation, action="read"):
    validate_schema(connection)
    policy.authorize(owner,workspace,"assistant",action)
    row = _row(connection,workspace,conversation,owner)
    expected = row["fence"]
    if authority(policy,owner,workspace,sorted(expected["modules"]),action) != expected:
        raise PolicyError(404,"not_found")
    return row


def public(row):
    return {key:row[key] for key in ("workspace_id","id","title","state","revision","created_at","updated_at","event_count")}


def audit(policy, owner, workspace, conversation, revision):
    key = hashlib.sha256(encode([workspace,conversation]).encode("ascii")).hexdigest()
    policy._changed(owner,workspace,"resource.update","assistant:"+key,revision)


def create_conversation(store, *, owner, workspace, conversation, title, required_modules=("assistant",),
                        clock=lambda:datetime.now(timezone.utc)):
    """Trusted adapter ingress for a LOCAL saved display record, not a native session.

    No HTTP route, automatic enrollment, process, tool, network or provider locator.
    The owner must already have current assistant write and every dependency read.
    """
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("configured_store_required")
    for value in (owner,workspace,conversation):identifier(value)
    text(title,limit=800)
    if type(required_modules) is not tuple:
        raise ValueError("explicit_dependencies_required")
    modules=sorted(set(required_modules))
    if len(modules) != len(required_modules):raise ValueError("duplicate_dependencies")
    def create(policy,connection):
        validate_schema(connection)
        fence=authority(policy,owner,workspace,modules,"write")
        if connection.execute("SELECT COUNT(*) FROM assistant_conversations WHERE workspace_id=? AND owner_id=?",(workspace,owner)).fetchone()[0] >= 100:
            raise PolicyError(409,"conversation_limit")
        if connection.execute("SELECT 1 FROM assistant_conversations WHERE workspace_id=? AND id=?",(workspace,conversation)).fetchone():
            raise PolicyError(409,"conversation_exists")
        now=_now(clock)
        connection.execute("INSERT INTO assistant_conversations VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",(workspace,"assistant",conversation,owner,title,"active",1,now,now,encode(fence),0,0))
        audit(policy,owner,workspace,conversation,1)
        return public(_row(connection,workspace,conversation,owner))
    return store._transaction(create,write=True,with_connection=True)


def append_events(store, *, owner, workspace, conversation, events, expected_revision,
                  clock=lambda:datetime.now(timezone.utc)):
    """Trusted, sanitized ingress only. Reauthorizes and fences the complete batch.

    This deliberately does not accept raw ACP updates. A future producer must
    implement source-specific redaction and permissions before ingesting real data.
    Archive/restore and policy changes cannot silently grant continuation rights.
    """
    if type(store) is not SQLiteWorkspaceService:
        raise ValueError("configured_store_required")
    for value in (owner,workspace,conversation):identifier(value)
    if type(events) is not tuple or not 1 <= len(events) <= 100 or any(type(e) is not DisplayEvent for e in events):
        raise ValueError("bounded_events_required")
    encoded=[encode(event.as_dict()) for event in events]
    def append(policy,connection):
        row=authorized_row(policy,connection,owner,workspace,conversation,"write")
        policy._match(expected_revision,row["revision"])
        if row["state"] != "active":raise PolicyError(409,"conversation_archived")
        if row["revision"] == MAX_REVISION or row["event_count"]+len(events)>MAX_EVENTS or row["event_bytes"]+sum(map(len,encoded))>MAX_BYTES:
            raise PolicyError(409,"conversation_limit")
        now=_now(clock)
        if timestamp(now)<timestamp(row["updated_at"]):raise ValueError("clock_moved_backwards")
        connection.executemany("INSERT INTO assistant_events VALUES (?,?,?,?)",[(workspace,conversation,row["event_count"]+i+1,value) for i,value in enumerate(encoded)])
        connection.execute("UPDATE assistant_conversations SET revision=revision+1,updated_at=?,event_count=event_count+?,event_bytes=event_bytes+? WHERE workspace_id=? AND id=?",(now,len(encoded),sum(map(len,encoded)),workspace,conversation))
        audit(policy,owner,workspace,conversation,row["revision"]+1)
        return public(_row(connection,workspace,conversation,owner))
    return store._transaction(append,write=True,with_connection=True)
