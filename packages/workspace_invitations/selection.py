"""One recipient identity observation, not a credential or authority proposal."""
from hashlib import sha256
import json
import re

from packages.workspaces import PolicyError
from packages.workspaces.authority import principal_pin


_MISSING = object()
_VALUE = re.compile(r"irs1-[0-9a-f]{64}\Z")


def _precondition(policy, actor, workspace, principal_id, role):
    # Purpose/identity equality only. Never a secret, MAC, permission, reservation,
    # TTL, global policy epoch or mutable membership/lifecycle revision.
    value = {"version": 1, "purpose": "workspace.invitation.create.recipient",
             "realm": policy._realm, "workspace_id": workspace, "role": role,
             "actor": principal_pin(policy, actor),
             "target": principal_pin(policy, principal_id)}
    encoded = json.dumps(value, ensure_ascii=True, sort_keys=True,
                         separators=(",", ":")).encode("ascii")
    return "irs1-" + sha256(encoded).hexdigest()


def recipient_selection(policy, actor, workspace, principal_id, role):
    """Review one exact target after the existing invitation-admin gates.

    No invitation-table lookup or reservation: an already invited/member target
    can still be reviewed. Actual first-write business eligibility is separate.
    The surrounding ordinary policy loader retains its global integrity checks.
    """
    member = policy._member(actor, workspace)
    if member.role != "admin":
        raise PolicyError(403, "forbidden")
    policy._admin(actor, workspace)
    target = policy._principals.get(principal_id) if type(principal_id) is str else None
    if target is None or not target.active:
        raise PolicyError(404, "invitation_recipient_unavailable")
    if type(role) is not str or role not in {"viewer", "developer"}:
        raise PolicyError(400, "invalid_invitation_role")
    return {"workspace_id": workspace, "principal_id": principal_id, "role": role,
            "precondition": _precondition(policy, actor, workspace, principal_id, role)}


def require_recipient_selection(policy, actor, workspace, principal_id, role,
                                expected=_MISSING):
    """Fresh-write equality check, only after current gates and retry lookup."""
    if expected is _MISSING:
        raise PolicyError(428, "invitation_recipient_selection_required")
    if type(expected) is not str or len(expected) != 69 or _VALUE.fullmatch(expected) is None:
        raise PolicyError(400, "invalid_invitation_recipient_selection")
    if expected != _precondition(policy, actor, workspace, principal_id, role):
        raise PolicyError(412, "invitation_recipient_selection_changed")
