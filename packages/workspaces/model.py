"""Immutable policy inputs. All records are supplied by a trusted server adapter."""
from dataclasses import dataclass

# JSON/JavaScript preconditions and every persisted policy counter share this bound.
MAX_REVISION = 9007199254740991


def identifier(value: str) -> None:
    if not isinstance(value, str) or not value or len(value) > 200 or any(c.isspace() for c in value):
        raise ValueError("invalid identifier")


def revision(value: int) -> None:
    if type(value) is not int or not 1 <= value <= MAX_REVISION:
        raise ValueError("revision must be a positive safe integer")


@dataclass(frozen=True)
class Principal:
    id: str
    issuer: str
    subject: str
    active: bool = True
    generation: int = 1
    authority_epoch: int = 1

    def __post_init__(self):
        for value in (self.id, self.issuer, self.subject):
            identifier(value)
        if type(self.active) is not bool:
            raise ValueError("invalid principal state")
        for value in (self.generation, self.authority_epoch):
            if type(value) is not int or not 1 <= value <= MAX_REVISION:
                raise ValueError("invalid principal authority generation")


@dataclass(frozen=True)
class Workspace:
    id: str
    name: str
    kind: str = "product"
    state: str = "active"
    revision: int = 1

    def __post_init__(self):
        identifier(self.id)
        if not isinstance(self.name, str) or not self.name.strip() or len(self.name) > 200:
            raise ValueError("invalid workspace name")
        if self.kind not in {"product", "shared"} or self.state not in {"active", "suspended", "archived"}:
            raise ValueError("invalid workspace kind/state")
        revision(self.revision)


@dataclass(frozen=True)
class Membership:
    workspace_id: str
    principal_id: str
    role: str
    state: str = "active"
    revision: int = 1

    def __post_init__(self):
        identifier(self.workspace_id)
        identifier(self.principal_id)
        if self.role not in {"viewer", "developer", "admin"} or self.state not in {"active", "revoked"}:
            raise ValueError("invalid membership role/state")
        revision(self.revision)


@dataclass(frozen=True)
class ModuleInstance:
    workspace_id: str
    key: str
    capabilities: tuple[str, ...] = ("read",)
    state: str = "enabled"
    revision: int = 1

    def __post_init__(self):
        identifier(self.workspace_id)
        identifier(self.key)
        if type(self.capabilities) is not tuple or not set(self.capabilities) <= {"read", "write"}:
            raise ValueError("unsupported module capabilities")
        if len(set(self.capabilities)) != len(self.capabilities) or self.state not in {"enabled", "disabling", "disabled"}:
            raise ValueError("invalid module capabilities/state")
        revision(self.revision)


@dataclass(frozen=True)
class ModuleStatus:
    """Minimal stored lifecycle observation, never resource authority/config."""
    workspace_id: str
    key: str
    state: str
    revision: int

    def __post_init__(self):
        identifier(self.workspace_id)
        identifier(self.key)
        if self.state not in {"enabled", "disabling", "disabled"}:
            raise ValueError("invalid module state")
        revision(self.revision)


@dataclass(frozen=True)
class Resource:
    workspace_id: str
    module_key: str
    id: str
    text: str
    revision: int = 1

    def __post_init__(self):
        for value in (self.workspace_id, self.module_key, self.id):
            identifier(value)
        if not isinstance(self.text, str) or len(self.text) > 10000:
            raise ValueError("invalid resource text")
        revision(self.revision)


@dataclass(frozen=True)
class Decision:
    """An observation, NEVER a reusable capability or authority token."""
    principal_id: str
    workspace_id: str
    module_key: str
    action: str
    policy_epoch: int


@dataclass(frozen=True)
class AuditEvent:
    """Metadata only. Backing store determines durability; not an outbox."""
    sequence: int
    actor_id: str
    workspace_id: str
    action: str
    object_id: str
    revision: int

    def __post_init__(self):
        revision(self.sequence)
        revision(self.revision)
