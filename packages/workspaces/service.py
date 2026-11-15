"""Single-process reference policy store; not authentication or persistence.

A trusted adapter must supply a CURRENT authenticated principal ID. This module
must not be wired directly to a client-provided subject/header. All resource
access here reauthorizes under the same lock as the read/write. Decisions cannot
be presented instead of current authorization. External stores/workers must build
their own transaction/revocation integration before making isolation claims.
"""
from dataclasses import replace
from threading import RLock
from uuid import uuid4

from .model import AuditEvent, Decision, Membership, ModuleInstance, Principal, Resource, Workspace


class PolicyError(Exception):
    def __init__(self, status: int, code: str):
        self.status, self.code = status, code
        super().__init__(code)  # No hidden IDs, raw payloads or directory metadata.


def _unique(records, key):
    result = {}
    for record in records:
        identity = key(record)
        if identity in result:
            raise ValueError("duplicate policy identity")
        result[identity] = record
    return result


class WorkspaceService:
    """Trusted bootstrap data; no invitation, HTTP, secret or connector API.

    Membership enrollment is deliberately bootstrap-only until verified,
    single-use invitation acceptance is implemented. Existing memberships can be
    demoted/revoked, but revoked memberships cannot be resurrected through edits.
    """

    def __init__(self, *, principals: tuple[Principal, ...], workspaces: tuple[Workspace, ...],
                 memberships: tuple[Membership, ...], modules: tuple[ModuleInstance, ...],
                 resources: tuple[Resource, ...] = (), operators: frozenset[str] = frozenset()):
        self._lock = RLock()
        self._principals = _unique(principals, lambda p: p.id)
        _unique(principals, lambda p: (p.issuer, p.subject))
        self._workspaces = _unique(workspaces, lambda w: w.id)
        self._memberships = _unique(memberships, lambda m: (m.workspace_id, m.principal_id))
        self._modules = _unique(modules, lambda m: (m.workspace_id, m.key))
        self._resources = _unique(resources, lambda r: (r.workspace_id, r.module_key, r.id))
        self._operators = frozenset(operators)
        self._epoch = 1
        self._audit = []
        if not self._operators <= self._principals.keys():
            raise ValueError("unknown operator")
        for m in memberships:
            if m.workspace_id not in self._workspaces or m.principal_id not in self._principals:
                raise ValueError("membership has foreign reference")
        for module in modules:
            if module.workspace_id not in self._workspaces:
                raise ValueError("module has foreign reference")
        for resource in resources:
            if (resource.workspace_id, resource.module_key) not in self._modules:
                raise ValueError("resource has foreign reference")
        for workspace in workspaces:
            if not self._eligible_admins(workspace.id):
                raise ValueError("workspace requires an active eligible admin")

    def _principal(self, actor_id):
        principal = self._principals.get(actor_id) if isinstance(actor_id, str) else None
        if principal is None or not principal.active:
            raise PolicyError(401, "unauthenticated")
        return principal

    def _member(self, actor_id, workspace_id):
        self._principal(actor_id)
        member = self._memberships.get((workspace_id, actor_id))
        if workspace_id not in self._workspaces or member is None or member.state != "active":
            raise PolicyError(404, "not_found")
        return member

    def _admin(self, actor_id, workspace_id, *, allow_archived=False):
        member = self._member(actor_id, workspace_id)
        state = self._workspaces[workspace_id].state
        if state != "active" and not (allow_archived and state == "archived"):
            raise PolicyError(409, "workspace_unavailable")
        if member.role != "admin":
            raise PolicyError(403, "forbidden")
        return member

    def _operator(self, actor_id):
        self._principal(actor_id)
        if actor_id not in self._operators:
            raise PolicyError(403, "forbidden")

    @staticmethod
    def _match(expected, current):
        if expected is None:
            raise PolicyError(428, "precondition_required")
        if type(expected) is not int or expected < 1:
            raise PolicyError(400, "invalid_revision")
        if expected != current:
            raise PolicyError(412, "stale_revision")

    def _eligible_admins(self, workspace_id):
        return [m for m in self._memberships.values() if m.workspace_id == workspace_id
                and m.state == "active" and m.role == "admin" and self._principals[m.principal_id].active]

    def _changed(self, actor, workspace, action, object_id, revision, *, policy=False):
        if policy:
            self._epoch += 1
        self._audit.append(AuditEvent(len(self._audit) + 1, actor, workspace, action, object_id, revision))

    def _authorize(self, actor_id, workspace_id, module_key, action):
        member = self._member(actor_id, workspace_id)
        workspace = self._workspaces[workspace_id]
        if workspace.state == "suspended" or (workspace.state == "archived" and action != "read"):
            raise PolicyError(409, "workspace_unavailable")
        module = self._modules.get((workspace_id, module_key))
        if module is None:
            raise PolicyError(404, "not_found")
        if module.state != "enabled":
            raise PolicyError(409, "module_unavailable")
        if action not in {"read", "write"} or action not in module.capabilities:
            raise PolicyError(403, "forbidden")
        if action == "write" and member.role == "viewer":
            raise PolicyError(403, "forbidden")
        return Decision(actor_id, workspace_id, module_key, action, self._epoch)

    def authorize(self, actor_id, workspace_id, module_key, action):
        """Check policy now. Returned metadata must not be reused as permission."""
        with self._lock:
            return self._authorize(actor_id, workspace_id, module_key, action)

    def list_workspaces(self, actor_id):
        with self._lock:
            self._principal(actor_id)
            return tuple(sorted((w for w in self._workspaces.values()
                if (m := self._memberships.get((w.id, actor_id))) is not None
                and m.state == "active" and w.state != "suspended"), key=lambda w: w.id))

    def list_memberships(self, actor_id, workspace_id):
        with self._lock:
            self._admin(actor_id, workspace_id, allow_archived=True)
            return tuple(sorted((m for m in self._memberships.values() if m.workspace_id == workspace_id),
                                key=lambda m: m.principal_id))

    def set_membership(self, actor_id, workspace_id, principal_id, *, role, state, expected_revision):
        """Edit a member; archives permit only authority reduction/revocation."""
        with self._lock:
            self._admin(actor_id, workspace_id, allow_archived=True)
            old = self._memberships.get((workspace_id, principal_id))
            if old is None:
                raise PolicyError(404, "not_found")
            self._match(expected_revision, old.revision)
            if old.state != "active":
                raise PolicyError(409, "membership_revoked")
            try:
                new = replace(old, role=role, state=state, revision=old.revision + 1)
            except ValueError:
                raise PolicyError(400, "invalid_membership") from None
            rank = {"viewer": 0, "developer": 1, "admin": 2}
            if self._workspaces[workspace_id].state == "archived" and rank[new.role] > rank[old.role]:
                raise PolicyError(409, "archive_privilege_increase")
            if new.state == "active" and not self._principals[principal_id].active:
                raise PolicyError(409, "principal_inactive")
            remaining = [m for m in self._eligible_admins(workspace_id) if m.principal_id != principal_id]
            if not remaining and not (new.state == "active" and new.role == "admin"
                                      and self._principals[principal_id].active):
                raise PolicyError(409, "last_admin")
            self._memberships[(workspace_id, principal_id)] = new
            self._changed(actor_id, workspace_id, "membership.update", principal_id, new.revision, policy=True)
            return new

    def transfer_admin(self, actor_id, workspace_id, replacement_id, *, expected_revision, replacement_revision):
        """Atomically promote an eligible member and demote the acting admin."""
        with self._lock:
            previous = self._admin(actor_id, workspace_id)
            replacement = self._memberships.get((workspace_id, replacement_id))
            if replacement is None:
                raise PolicyError(404, "not_found")
            self._match(expected_revision, previous.revision)
            self._match(replacement_revision, replacement.revision)
            if actor_id == replacement_id or replacement.state != "active" or not self._principals[replacement_id].active:
                raise PolicyError(409, "replacement_ineligible")
            promoted = replace(replacement, role="admin", revision=replacement.revision + 1)
            demoted = replace(previous, role="developer", revision=previous.revision + 1)
            self._memberships[(workspace_id, replacement_id)] = promoted
            self._memberships[(workspace_id, actor_id)] = demoted
            self._changed(actor_id, workspace_id, "membership.promote", replacement_id, promoted.revision, policy=True)
            self._changed(actor_id, workspace_id, "membership.demote", actor_id, demoted.revision)
            return demoted, promoted

    def list_control_workspaces(self, actor_id):
        """Minimal operator lifecycle metadata, never membership or module data."""
        with self._lock:
            self._operator(actor_id)
            return tuple(sorted(self._workspaces.values(), key=lambda w: w.id))

    def list_modules(self, actor_id, workspace_id):
        with self._lock:
            self._admin(actor_id, workspace_id, allow_archived=True)
            return tuple(sorted((m for m in self._modules.values()
                                 if m.workspace_id == workspace_id), key=lambda m: m.key))

    def install_module(self, actor_id, workspace_id, module_key, *, capabilities):
        """Trusted catalog capabilities only; new instances start denied/disabled."""
        with self._lock:
            self._admin(actor_id, workspace_id)
            if (workspace_id, module_key) in self._modules:
                raise PolicyError(409, "module_exists")
            try:
                module = ModuleInstance(workspace_id, module_key, capabilities, "disabled")
            except (ValueError, TypeError):
                raise PolicyError(400, "invalid_module") from None
            self._modules[(workspace_id, module_key)] = module
            self._changed(actor_id, workspace_id, "module.install", module_key, 1, policy=True)
            return module

    def set_module_state(self, actor_id, workspace_id, module_key, *, state, expected_revision):
        with self._lock:
            self._admin(actor_id, workspace_id)
            old = self._modules.get((workspace_id, module_key))
            if old is None:
                raise PolicyError(404, "not_found")
            self._match(expected_revision, old.revision)
            transitions = {"enabled": {"disabling"}, "disabling": {"disabled"}, "disabled": {"enabled"}}
            if state not in transitions[old.state]:
                raise PolicyError(409, "invalid_transition")
            new = replace(old, state=state, revision=old.revision + 1)
            self._modules[(workspace_id, module_key)] = new
            self._changed(actor_id, workspace_id, "module.state", module_key, new.revision, policy=True)
            return new

    def get_module(self, actor_id, workspace_id, module_key):
        """Only admins can inspect a disabling/disabled module lifecycle."""
        with self._lock:
            member = self._member(actor_id, workspace_id)
            if self._workspaces[workspace_id].state == "suspended":
                raise PolicyError(409, "workspace_unavailable")
            module = self._modules.get((workspace_id, module_key))
            if module is None:
                raise PolicyError(404, "not_found")
            if module.state != "enabled" and member.role != "admin":
                raise PolicyError(409, "module_unavailable")
            return module

    def set_workspace_state(self, actor_id, workspace_id, *, state, expected_revision):
        with self._lock:
            self._operator(actor_id)
            old = self._workspaces.get(workspace_id)
            if old is None:
                raise PolicyError(404, "not_found")
            self._match(expected_revision, old.revision)
            transitions = {"active": {"suspended", "archived"}, "suspended": {"active", "archived"}, "archived": set()}
            if state not in transitions[old.state]:
                raise PolicyError(409, "invalid_transition")
            new = replace(old, state=state, revision=old.revision + 1)
            self._workspaces[workspace_id] = new
            self._changed(actor_id, workspace_id, "workspace.state", workspace_id, new.revision, policy=True)
            return new

    def create_workspace(self, actor_id, *, name, kind, initial_admin):
        with self._lock:
            self._operator(actor_id)
            if initial_admin not in self._principals or not self._principals[initial_admin].active:
                raise PolicyError(400, "invalid_initial_admin")
            try:
                workspace = Workspace(str(uuid4()), name, kind)
            except ValueError:
                raise PolicyError(400, "invalid_workspace") from None
            self._workspaces[workspace.id] = workspace
            self._memberships[(workspace.id, initial_admin)] = Membership(workspace.id, initial_admin, "admin")
            self._changed(actor_id, workspace.id, "workspace.create", workspace.id, 1, policy=True)
            return workspace

    def read_resource(self, actor_id, workspace_id, module_key, resource_id):
        with self._lock:
            self._authorize(actor_id, workspace_id, module_key, "read")
            resource = self._resources.get((workspace_id, module_key, resource_id))
            if resource is None:
                raise PolicyError(404, "not_found")
            return resource

    def list_resources(self, actor_id, workspace_id, module_key, *, offset=0, limit=20):
        with self._lock:
            self._authorize(actor_id, workspace_id, module_key, "read")
            if type(offset) is not int or type(limit) is not int or offset < 0 or not 1 <= limit <= 100:
                raise PolicyError(400, "invalid_page")
            owned = sorted((r for r in self._resources.values()
                if r.workspace_id == workspace_id and r.module_key == module_key), key=lambda r: r.id)
            return len(owned), tuple(owned[offset:offset + limit])

    def update_resource(self, actor_id, workspace_id, module_key, resource_id, *, text, expected_revision):
        with self._lock:
            self._authorize(actor_id, workspace_id, module_key, "write")
            old = self._resources.get((workspace_id, module_key, resource_id))
            if old is None:
                raise PolicyError(404, "not_found")
            self._match(expected_revision, old.revision)
            try:
                new = replace(old, text=text, revision=old.revision + 1)
            except ValueError:
                raise PolicyError(400, "invalid_resource") from None
            self._resources[(workspace_id, module_key, resource_id)] = new
            self._changed(actor_id, workspace_id, "resource.update", resource_id, new.revision)
            return new

    def audit_events(self, actor_id, workspace_id):
        with self._lock:
            self._admin(actor_id, workspace_id, allow_archived=True)
            return tuple(e for e in self._audit if e.workspace_id == workspace_id)
