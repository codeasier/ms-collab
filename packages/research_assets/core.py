"""Bounded business assets, independent of repository coordinates and execution."""
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
import hashlib
import json
import re

MAX_ASSETS = 1000
MAX_BATCH_BYTES = 2 * 1024 * 1024
MAX_DOCUMENT_BYTES = 32768
ID = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}\Z")
TYPES = {
    "api": ("API contracts", "contract", ("module", "command")),
    "records": ("Research records", "text", ()),
    "runbook": ("Runbooks", "sections", ("module", "status")),
    "fault": ("Fault knowledge", "sections", ("library", "category", "status", "confidence")),
}


class AssetError(ValueError):
    """Value-free failure. Never include documents, source paths or credentials."""


def identifier(value):
    if type(value) is not str or not ID.fullmatch(value):
        raise AssetError("invalid_identifier")
    return value


def text(value, maximum=256, *, empty=False, multiline=False):
    if (type(value) is not str or len(value) > maximum or (not empty and not value.strip())
            or value != value.strip() or any(not c.isprintable() and not (multiline and c in '\n\t') for c in value)):
        raise AssetError("invalid_text")
    try:
        value.encode("utf-8")
    except UnicodeError:
        raise AssetError("invalid_text") from None
    return value


def utc(value):
    try:
        if type(value) is not str or len(value) > 40:
            raise ValueError()
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if parsed.tzinfo is None or parsed.utcoffset() is None:
            raise ValueError()
        return parsed.astimezone(timezone.utc)
    except (ValueError, TypeError, OverflowError):
        raise AssetError("invalid_time") from None


def now_utc(clock):
    value = clock()
    if type(value) is not datetime or value.tzinfo is None or value.utcoffset() is None:
        raise AssetError("invalid_clock")
    return value.astimezone(timezone.utc)


def canonical(value):
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True, separators=(",", ":"), allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise AssetError("invalid_document") from None


def pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise AssetError("duplicate_field")
        result[key] = value
    return result


def decode(value, maximum=MAX_BATCH_BYTES):
    try:
        if type(value) is not str or len(value) > maximum:
            raise ValueError()
        return json.loads(value, object_pairs_hook=pairs, parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
    except (ValueError, TypeError, UnicodeError, RecursionError):
        raise AssetError("invalid_document") from None


@dataclass(frozen=True)
class AssetScope:
    workspace_id: str
    catalog_id: str

    def __post_init__(self):
        identifier(self.workspace_id); identifier(self.catalog_id)

    def as_dict(self):
        return asdict(self)


@dataclass(frozen=True)
class Entity:
    id: str
    title: str

    def __post_init__(self):
        identifier(self.id); text(self.title)


@dataclass(frozen=True)
class SourceSpec:
    id: str
    entity_ids: tuple[str, ...]
    type_ids: tuple[str, ...]

    def __post_init__(self):
        identifier(self.id)
        for values, allowed in ((self.entity_ids, None), (self.type_ids, TYPES)):
            if type(values) is not tuple or not 1 <= len(values) <= 32 or len(set(values)) != len(values):
                raise AssetError("invalid_source")
            for item in values:
                identifier(item)
                if allowed is not None and item not in allowed:
                    raise AssetError("unknown_type")


@dataclass(frozen=True)
class CatalogBinding:
    scope: AssetScope
    namespace: str
    entities: tuple[Entity, ...]
    sources: tuple[SourceSpec, ...]

    def __post_init__(self):
        if type(self.scope) is not AssetScope:
            raise AssetError("invalid_scope")
        identifier(self.namespace)
        for values, kind, limit in ((self.entities, Entity, 32), (self.sources, SourceSpec, 16)):
            if type(values) is not tuple or not 1 <= len(values) <= limit or any(type(v) is not kind for v in values):
                raise AssetError("invalid_binding")
            if len({v.id for v in values}) != len(values):
                raise AssetError("duplicate_binding")
        if any(not set(s.entity_ids) <= {e.id for e in self.entities} for s in self.sources):
            raise AssetError("unknown_entity")
        self.encode()  # Construction and durable read use one byte bound.

    def encode(self):
        encoded = canonical(asdict(self))
        if len(encoded) > 32768:
            raise AssetError("binding_too_large")
        return encoded

    @classmethod
    def decode(cls, raw):
        try:
            value = decode(raw, 32768)
            if set(value) != {"scope", "namespace", "entities", "sources"}:
                raise ValueError()
            if any(type(s) is not dict or set(s) != {'id', 'entity_ids', 'type_ids'} for s in value['sources']):
                raise ValueError()
            return cls(AssetScope(**value["scope"]), value["namespace"],
                       tuple(Entity(**e) for e in value["entities"]),
                       tuple(SourceSpec(s["id"], tuple(s["entity_ids"]), tuple(s["type_ids"])) for s in value["sources"]))
        except (ValueError, KeyError, TypeError):
            raise AssetError("invalid_binding") from None


@dataclass(frozen=True)
class SourceBatch:
    scope: AssetScope
    source_id: str
    revision: str
    observed_at: str
    documents: tuple[dict, ...]
    complete: bool


def _section(heading, value):
    return {"heading": heading, "text": text(value, 8192, empty=True, multiline=True)}


def project(binding, source, document):
    """Strict normalized DTO, not a Git/YAML/Markdown parser or secret scrubber.

    Layout adapters must select approved business fields before this boundary.
    Unknown fields fail instead of returning raw documents/operational evidence.
    """
    if type(document) is not dict or len(canonical(document)) > MAX_DOCUMENT_BYTES:
        raise AssetError("invalid_document")
    kind = document.get("type_id")
    common = {"type_id", "entity_id", "summary"}
    shapes = {
        "api": {"module", "command", "request", "response", "errors", "notes"},
        "records": {"id", "title", "text"},
        "runbook": {"id", "title", "module", "status", "preconditions", "steps", "expectations"},
        "fault": {"id", "title", "library", "category", "status", "confidence", "symptoms", "guidance"},
    }
    if type(kind) is not str or kind not in source.type_ids or set(document) != common | shapes[kind]:
        raise AssetError("invalid_document")
    entity = identifier(document["entity_id"])
    if entity not in source.entity_ids:
        raise AssetError("source_entity_mismatch")
    summary = text(document["summary"], 1024, empty=True, multiline=True)
    fields = {key: text(document[key]) for key in TYPES[kind][2]}
    if kind == "api":
        key = [document["module"], document["command"]]
        title = document["command"]
        sections = [_section(k.title(), document[k]) for k in ("request", "response", "errors", "notes")]
    else:
        explicit = text(document["id"])
        key = ["id", explicit] if kind != "fault" else ["library", document["library"], "id", explicit]
        title = text(document["title"])
        names = {"records": ("text",), "runbook": ("preconditions", "steps", "expectations"), "fault": ("symptoms", "guidance")}[kind]
        sections = [_section(k.title(), document[k]) for k in names]
    # Scope is part of the ID. Source, title, file layout and revision are not.
    identity = ["research-asset-v1", binding.scope.as_dict(), binding.namespace, entity, kind, key]
    asset_id = "a_" + hashlib.sha256(canonical(identity).encode("ascii")).hexdigest()
    public = {"id": asset_id, "type_id": kind, "title": title, "summary": summary,
              "entity": {"id": entity, "title": next(e.title for e in binding.entities if e.id == entity)}, "fields": fields}
    detail = {"format": TYPES[kind][1], "sections": sections, "execution_verified": False}
    revision = hashlib.sha256(canonical([public, detail]).encode("ascii")).hexdigest()
    return {**public, "content_revision": revision, "detail": detail}


def prepare_batch(binding, batches, now):
    if type(binding) is not CatalogBinding or type(batches) is not tuple or len(batches) != len(binding.sources):
        raise AssetError("incomplete_sources")
    if type(now) is not datetime or now.tzinfo is None or now.utcoffset() is None:
        raise AssetError("invalid_clock")
    supplied = {}
    for batch in batches:
        if type(batch) is not SourceBatch or type(batch.scope) is not AssetScope or batch.scope != binding.scope:
            raise AssetError("scope_mismatch")
        if batch.complete is not True:
            raise AssetError("incomplete_source")
        identifier(batch.source_id); identifier(batch.revision)
        if batch.source_id in supplied:
            raise AssetError("duplicate_source")
        if utc(batch.observed_at) > now:
            raise AssetError("future_observation")
        if type(batch.documents) is not tuple or len(batch.documents) > MAX_ASSETS:
            raise AssetError("invalid_documents")
        supplied[batch.source_id] = batch
    if set(supplied) != {s.id for s in binding.sources}:
        raise AssetError("incomplete_sources")
    assets, evidence, seen, total_bytes = [], {}, set(), 0
    for source in binding.sources:
        batch = supplied[source.id]
        projected = []
        for document in batch.documents:
            value = project(binding, source, document)
            if value["id"] in seen:
                raise AssetError("identity_conflict")
            seen.add(value["id"])
            projected.append(value)
            total_bytes += len(canonical(value))
            if len(seen) > MAX_ASSETS or total_bytes > MAX_BATCH_BYTES:
                raise AssetError("batch_too_large")
        projected.sort(key=lambda a: a["id"])
        evidence[source.id] = {"revision": batch.revision, "observed_at": utc(batch.observed_at).isoformat(),
                               "digest": hashlib.sha256(canonical(projected).encode("ascii")).hexdigest(), "count": len(projected)}
        assets.extend(projected)
    return tuple(sorted(assets, key=lambda a: (a["type_id"], a["title"], a["id"]))), evidence


def validate_summary(value, binding, *, asset_id, type_id, entity_id, title, digest):
    """Read-side schema/row/binding checks; never trust stored JSON as a DTO."""
    required = {"id", "type_id", "title", "summary", "entity", "fields", "content_revision"}
    if type(value) is not dict or set(value) != required:
        raise AssetError("invalid_summary")
    if value['id'] != asset_id or not re.fullmatch(r'a_[0-9a-f]{64}', asset_id):
        raise AssetError("invalid_summary")
    if value['type_id'] != type_id or type_id not in TYPES or value['title'] != title:
        raise AssetError("invalid_summary")
    text(title); text(value['summary'], 1024, empty=True, multiline=True)
    entities = {e.id:e.title for e in binding.entities}
    if entity_id not in entities or value['entity'] != {'id':entity_id, 'title':entities[entity_id]}:
        raise AssetError("invalid_summary")
    fields = value['fields']
    if type(fields) is not dict or set(fields) != set(TYPES[type_id][2]):
        raise AssetError("invalid_summary")
    for item in fields.values():
        text(item)
    if type_id == 'api' and fields['command'] != title:
        raise AssetError("invalid_summary")
    if type(value['content_revision']) is not str or not re.fullmatch(r'[0-9a-f]{64}', value['content_revision']):
        raise AssetError("invalid_summary")
    if hashlib.sha256(canonical([binding.scope.as_dict(), value]).encode('ascii')).hexdigest() != digest:
        raise AssetError("invalid_summary")
    return value


def validate_detail(value, summary):
    kind = summary['type_id']
    headings = {'api':('Request','Response','Errors','Notes'), 'records':('Text',),
                'runbook':('Preconditions','Steps','Expectations'), 'fault':('Symptoms','Guidance')}[kind]
    if (type(value) is not dict or set(value) != {'format','sections','execution_verified'}
            or value['format'] != TYPES[kind][1] or value['execution_verified'] is not False
            or type(value['sections']) is not list or len(value['sections']) != len(headings)):
        raise AssetError("invalid_detail")
    for section, heading in zip(value['sections'], headings):
        if type(section) is not dict or set(section) != {'heading','text'} or section['heading'] != heading:
            raise AssetError("invalid_detail")
        text(section['text'], 8192, empty=True, multiline=True)
    public = {k:v for k,v in summary.items() if k != 'content_revision'}
    if hashlib.sha256(canonical([public, value]).encode('ascii')).hexdigest() != summary['content_revision']:
        raise AssetError("invalid_detail")
    return value
