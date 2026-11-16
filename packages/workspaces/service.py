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

from .authority import authority_current, capture_authority, validate_authority
from .model import MAX_REVISION, AuditEvent, Decision, Membership, ModuleInstance, Principal, Resource, Workspace, revision


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

    This core exposes no enrollment command. A trusted invitation adapter may
    add membership inside the shared policy transaction after verified single-use
    acceptance. Revoked memberships cannot be resurrected through ordinary edits.
    """

    def __init__(self, *, principals: tuple[Principal, ...], workspaces: tuple[Workspace, ...],
                 memberships: tuple[Membership, ...], modules: tuple[ModuleInstance, ...],
                 resources: tuple[Resource, ...] = (), operators: frozenset[str] = frozenset()):
        self._initialize_records(principals=principals, workspaces=workspaces,
                                 memberships=memberships, modules=modules,
                                 resources=resources, operators=operators,
                                 realm=uuid4().hex, epoch=1)
        # This constructor is a trusted NEW grant bootstrap, never a decoder.
        self._membership_authority = {
            key: capture_authority(self, (member.principal_id,))
            for key, member in self._memberships.items()}
        self._operator_authority = {
            actor: capture_authority(self, (actor,)) for actor in self._operators}
        for workspace in workspaces:
            if not self._eligible_admins(workspace.id):
                raise ValueError("workspace requires an active eligible admin")

    def _initialize_records(self, *, principals, workspaces, memberships, modules,
                            resources, operators, realm, epoch):
        self._lock = RLock()
        self._principals = _unique(principals, lambda p: p.id)
        _unique(principals, lambda p: (p.issuer, p.subject))
        self._workspaces = _unique(workspaces, lambda w: w.id)
        self._memberships = _unique(memberships, lambda m: (m.workspace_id, m.principal_id))
        self._modules = _unique(modules, lambda m: (m.workspace_id, m.key))
        self._resources = _unique(resources, lambda r: (r.workspace_id, r.module_key, r.id))
        self._operators = frozenset(operators)
        self._epoch, self._realm = epoch, realm
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

    @classmethod
    def _from_persisted(cls, *, principals, workspaces, memberships, modules,
                        resources, operators, realm, epoch,
                        membership_authority, operator_authority):
        """Decode retained records without granting authority to their local IDs.

        Legacy/unbound and stale grants are valid retained state, not eligible
        authority. In particular a store with no eligible admin must still load
        without inventing one. Only fresh bootstrap enforces the initial admin.
        """
        service = object.__new__(cls)
        service._initialize_records(principals=principals, workspaces=workspaces,
                                    memberships=memberships, modules=modules,
                                    resources=resources, operators=operators,
                                    realm=realm, epoch=epoch)
        service._membership_authority = membership_authority
        service._operator_authority = operator_authority
        service._validate_authority_grants()
        return service

    def _validate_authority_grants(self):
        if (type(self._membership_authority) is not dict
                or set(self._membership_authority) != set(self._memberships)
                or type(self._operator_authority) is not dict
                or set(self._operator_authority) != self._operators):
            raise ValueError("invalid_policy_authority")
        for (_, actor), grant in self._membership_authority.items():
            if grant is not None:
                validate_authority(grant, (actor,))
        for actor, grant in self._operator_authority.items():
            if grant is not None:
                validate_authority(grant, (actor,))

    def _membership_authority_current(self, actor_id, workspace_id):
        """Current ownership only; revoked rows retain their historical binding."""
        return ((workspace_id, actor_id) in self._memberships
                and authority_current(self, actor_id,
                    self._membership_authority.get((workspace_id, actor_id))))

    def _membership_current(self, actor_id, workspace_id):
        member = self._memberships.get((workspace_id, actor_id))
        return (member is not None and member.state == "active"
                and self._membership_authority_current(actor_id, workspace_id))

    def _operator_current(self, actor_id):
        return (actor_id in self._operators
                and authority_current(self, actor_id, self._operator_authority.get(actor_id)))

    def _grant_membership(self, member):
        """Trusted accepted-invitation/new-workspace seam, NOT a rebind API.

        Caller performs complete authorization and counter/audit preflight in
        the same transaction. A historical revoked row can only be re-enrolled
        for its still-current owner; stale and legacy ownership need separately
        reviewed recovery, never an ordinary invitation or silent recapture.
        """
        self._principal(member.principal_id)
        key = (member.workspace_id, member.principal_id)
        if member.workspace_id not in self._workspaces or member.state != "active":
            raise ValueError("invalid membership grant")
        prior = self._memberships.get(key)
        if prior is None:
            if member.revision != 1 or key in self._membership_authority:
                raise ValueError("invalid membership grant")
            grant = capture_authority(self, (member.principal_id,))
        else:
            if prior.state != "revoked" or member.revision != prior.revision + 1:
                raise ValueError("invalid membership grant")
            if not self._membership_authority_current(member.principal_id, member.workspace_id):
                raise PolicyError(409, "membership_authority_unavailable")
            grant = self._membership_authority[key]
        self._memberships[key] = member
        self._membership_authority[key] = grant

    def set_principal(self, *args, **kwargs):
        """Unavailable until every stored ACL and long-I/O pin is generation-bound.

        No trusted-host bypass, enable flag or provisional transition ships here.
        """
        raise PolicyError(503, "identity_transition_unavailable")

    def _principal(self, actor_id):
        principal = self._principals.get(actor_id) if isinstance(actor_id, str) else None
        if principal is None or not principal.active:
            raise PolicyError(401, "unauthenticated")
        return principal

    def _member(self, actor_id, workspace_id):
        self._principal(actor_id)
        member = self._memberships.get((workspace_id, actor_id))
        if workspace_id not in self._workspaces or not self._membership_current(actor_id, workspace_id):
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
        if not self._operator_current(actor_id):
            raise PolicyError(403, "forbidden")

    @staticmethod
    def _match(expected, current):
        if expected is None:
            raise PolicyError(428, "precondition_required")
        if type(expected) is not int or not 1 <= expected <= MAX_REVISION:
            raise PolicyError(400, "invalid_revision")
        if expected != current:
            raise PolicyError(412, "stale_revision")

    def _eligible_admins(self, workspace_id):
        return [m for m in self._memberships.values() if m.workspace_id == workspace_id
                and m.role == "admin" and self._membership_current(m.principal_id, workspace_id)]

    def _check_change(self, *revisions, policy=False, audits=1):
        """Budget the WHOLE operation before changing memory or adapter tables.

        Trusted internal seam, not a counter reset/reservation or public option.
        Call under the same lock/transaction as the mutations. Multi-row writers
        must include every changed revision and every success-audit event.
        """
        count = len(self._audit)
        if (type(audits) is not int or not 1 <= audits <= MAX_REVISION
                or type(count) is not int or not 0 <= count <= MAX_REVISION):
            raise PolicyError(503, "invalid_counter")
        try:
            revision(self._epoch)
            for value in revisions:
                revision(value)
        except ValueError:
            raise PolicyError(503, "invalid_counter") from None
        if (any(value == MAX_REVISION for value in revisions)
                or (policy and self._epoch == MAX_REVISION)
                or count > MAX_REVISION - audits):
            raise PolicyError(409, "counter_exhausted")

    def _changed(self, actor, workspace, action, object_id, revision, *, policy=False):
        # Final guard for trusted SQL adapters: failures roll back their shared
        # transaction. Core in-memory methods also preflight before any mutation.
        self._check_change(policy=policy)
        event = AuditEvent(len(self._audit) + 1, actor, workspace, action, object_id, revision)
        if policy:
            self._epoch += 1
        self._audit.append(event)

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
                if self._membership_current(actor_id, w.id) and w.state != "suspended"), key=lambda w: w.id))

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
                new = replace(old, role=role, state=state)
            except ValueError:
                raise PolicyError(400, "invalid_membership") from None
            rank = {"viewer": 0, "developer": 1, "admin": 2}
            if self._workspaces[workspace_id].state == "archived" and rank[new.role] > rank[old.role]:
                raise PolicyError(409, "archive_privilege_increase")
            if new.state == "active" and not self._principals[principal_id].active:
                raise PolicyError(409, "principal_inactive")
            if new.state == "active" and not self._membership_authority_current(principal_id, workspace_id):
                raise PolicyError(409, "membership_authority_unavailable")
            remaining = [m for m in self._eligible_admins(workspace_id) if m.principal_id != principal_id]
            if not remaining and not (new.state == "active" and new.role == "admin"
                                      and self._membership_authority_current(principal_id, workspace_id)):
                raise PolicyError(409, "last_admin")
            self._check_change(old.revision, policy=True)
            new = replace(new, revision=old.revision + 1)
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
            if actor_id == replacement_id or not self._membership_current(replacement_id, workspace_id):
                raise PolicyError(409, "replacement_ineligible")
            self._check_change(previous.revision, replacement.revision, policy=True, audits=2)
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
            self._check_change(policy=True)
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
            self._check_change(old.revision, policy=True)
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
            self._check_change(old.revision, policy=True)
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
            self._check_change(policy=True)
            self._workspaces[workspace.id] = workspace
            self._grant_membership(Membership(workspace.id, initial_admin, "admin"))
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
                new = replace(old, text=text)
            except ValueError:
                raise PolicyError(400, "invalid_resource") from None
            self._check_change(old.revision)
            new = replace(new, revision=old.revision + 1)
            self._resources[(workspace_id, module_key, resource_id)] = new
            self._changed(actor_id, workspace_id, "resource.update", resource_id, new.revision)
            return new

    def audit_events(self, actor_id, workspace_id):
        with self._lock:
            self._admin(actor_id, workspace_id, allow_archived=True)
            return tuple(e for e in self._audit if e.workspace_id == workspace_id)
