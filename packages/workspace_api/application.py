"""Small WSGI API connecting trusted verification to atomic SQLite policy.

There is no server command, default verifier, anonymous or mock identity.
An explicit session boundary can replace (never mix with) bearer verification.
Test doubles exist only under tests/.
"""
from contextlib import closing
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from http import HTTPStatus
import json
import re
import sqlite3
from urllib.parse import parse_qsl

from packages.identity import AuthenticationError, AuthenticationUnavailable, Credential, CredentialVerifier
from packages.workspaces import PolicyError, SQLiteWorkspaceService, StorageError
from packages.workspaces.model import MAX_REVISION

_SEGMENT = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,199}\Z")
_REVISION = re.compile(r'"([1-9][0-9]{0,15})"\Z')
_MAX_BODY = 16384


def _invalid():
    return PolicyError(400, "invalid_request")


@dataclass(frozen=True)
class _ConflictingWorkspaceHint:
    """Request-local rejection intent, never an identity or authority grant."""
    assertion: object = field(repr=False)
    workspace: str
    control: bool


def _workspace_hint(assertion, environ):
    """Capture disagreement only; the router still decides whether a route exists.

    Do not retain the raw hint or reinterpret aliases, unscoped routes, or trusted
    Python callers without a path. Authentication and route visibility are checked
    later under the existing transaction, not by this request-shape function.
    """
    if "HTTP_X_WORKSPACE_ID" not in environ:
        return assertion
    path = environ.get("PATH_INFO")
    if type(path) is not str or len(path) > 1000:
        return assertion
    parts = path.split("/")
    control = False
    if len(parts) >= 5 and parts[:3] == ["", "api", "workspaces"]:
        workspace = parts[3]
    elif len(parts) == 5 and parts[:4] == ["", "api", "control", "workspaces"]:
        workspace, control = parts[4], True
    else:
        return assertion
    if not _SEGMENT.fullmatch(workspace):
        return assertion
    hint = environ["HTTP_X_WORKSPACE_ID"]
    if type(hint) is str and hint == workspace:
        return assertion
    return _ConflictingWorkspaceHint(assertion, workspace, control)


def _workspace_hint_operation(assertion, callback):
    if type(assertion) is not _ConflictingWorkspaceHint:
        return assertion, callback
    workspace, control = assertion.workspace, assertion.control

    def reject(policy, actor, connection=None, receipts=None):
        # Same current authentication/snapshot as an ordinary operation. Never
        # inspect the header's target, authorize by it, or run business effects.
        if control:
            policy._operator(actor)
            if workspace not in policy._workspaces:
                raise PolicyError(404, "not_found")
        else:
            policy._member(actor, workspace)
        raise _invalid()

    return assertion.assertion, reject


def _object(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise ValueError("duplicate field")
        result[key] = value
    return result


class WorkspaceApplication:
    """Trusted application factory. Config is never taken from request fields.

    A deployment MUST supply a reviewed real verifier and a preinitialized store.
    WSGI servers/proxies must supply bounded request reads, TLS, header normalization
    and limits. This adapter does not make the existing local demo authenticated.
    """
    def __init__(self, *, store: SQLiteWorkspaceService | None = None, providers=None, provider_key=None,
                 clock=lambda: datetime.now(timezone.utc), session_boundary=None):
        self._session_boundary = None
        if session_boundary is not None:
            # Trusted constructor composition only. No assertion conversion or
            # fallback between cookies and bearer authentication is permitted.
            from packages.session_api import SessionWorkspaceApplication
            if (type(session_boundary) is not SessionWorkspaceApplication
                    or store is not None or providers is not None or provider_key is not None):
                raise ValueError("select exactly one explicit authentication boundary")
            self._session_boundary = session_boundary
            return
        if type(store) is not SQLiteWorkspaceService:
            raise ValueError("an initialized SQLite policy store is required")
        providers = tuple(providers)
        if type(provider_key) is not str or provider_key not in {p.key for p in providers}:
            raise ValueError("select an explicit configured provider")
        self._store = store
        self._provider_key = provider_key
        self._verifier = CredentialVerifier(providers=providers, clock=clock)

    def __call__(self, environ, start_response):
        try:
            result = self._dispatch(environ)
            status, body = 200, {"data": result}
        except AuthenticationError:
            status, body = 401, {"error": "authentication_required"}
        except AuthenticationUnavailable:
            status, body = 503, {"error": "authentication_unavailable"}
        except StorageError:
            status, body = 503, {"error": "storage_unavailable"}
        except PolicyError as error:
            status, body = error.status, {"error": error.code}
        except Exception:
            # No provider, database, credential, body or environment in responses.
            status, body = 500, {"error": "internal_error"}
        payload = json.dumps(body, ensure_ascii=True, separators=(",", ":")).encode("ascii")
        headers = [("Content-Type", "application/json; charset=utf-8"),
                   ("Content-Length", str(len(payload))), ("Cache-Control", "no-store"),
                   ("X-Content-Type-Options", "nosniff"), ("Vary", "Authorization")]
        if status == 401:
            headers.append(("WWW-Authenticate", "Bearer"))
        start_response(f"{status} {HTTPStatus(status).phrase}", headers)
        return [payload]

    def _verify_request(self, environ):
        """Verify an opaque bearer credential using trusted factory configuration."""
        if self._session_boundary is not None:
            return self._session_boundary._verify_request(environ)
        authorization = environ.get("HTTP_AUTHORIZATION", "")
        if type(authorization) is not str or not authorization.startswith("Bearer "):
            raise AuthenticationError()
        try:
            credential = Credential(authorization[7:])
        except ValueError:
            raise AuthenticationError() from None
        # Protocol verification (possibly network I/O) occurs before any DB lock.
        return _workspace_hint(self._verifier.verify(self._provider_key, credential), environ)

    def _authenticated_transaction(self, assertion, callback, *, write=False, with_connection=False, before_commit=None, receipt_only=None, storage_admission=None):
        """Internal adapter seam, never an HTTP callback or reusable authority.

        Resolve the current subject binding and evaluate the complete adapter
        operation in the same policy snapshot. Callback is trusted Python code.
        """
        if storage_admission is not None and (not callable(storage_admission)
                or not with_connection or receipt_only is not None):
            raise ValueError("invalid trusted storage admission")
        assertion, callback = _workspace_hint_operation(assertion, callback)
        if self._session_boundary is not None:
            return self._session_boundary._authenticated_transaction(assertion, callback,
                write=write, with_connection=with_connection, before_commit=before_commit, receipt_only=receipt_only,
                storage_admission=storage_admission)
        if receipt_only is not None:
            from packages.directory_cursors.transaction import run, validate_mode
            validate_mode(receipt_only, write=write, with_connection=with_connection, before_commit=before_commit)
            if not callable(callback):
                raise ValueError('trusted receipt callback required')
            def current(policy, connection):
                return self._current_bearer_actor(assertion, policy)
            try:
                with closing(self._store._connect()) as connection:
                    return run(connection, self._store, receipt_only, callback, current)
            except (sqlite3.Error, OSError):
                raise PolicyError(503, 'scope_directory_unavailable') from None
        def operation(policy, connection=None):
            # Only an explicitly mounted trusted adapter supplies this read-only
            # structural check. It must precede current-auth/business denials.
            if storage_admission is not None and storage_admission(connection) is not None:
                raise ValueError("invalid trusted storage admission result")
            # Recheck after waiting for the lock, not merely when token arrived.
            actor = self._current_bearer_actor(assertion, policy)
            result = (callback(policy, actor, connection) if with_connection
                      else callback(policy, actor))
            # An expiry during policy evaluation rolls back mutations and audit.
            self._verifier.validate_current(self._provider_key, assertion)
            return result

        def commit_guard():
            if before_commit is not None:
                before_commit()  # Trusted adapters may only ADD a commit guard.
            # Remain the last guard: adapter work may itself consume validity.
            self._verifier.validate_current(self._provider_key, assertion)
        return self._store._transaction(operation, write=write, with_connection=with_connection,
                                        before_commit=commit_guard)

    def _current_bearer_actor(self, assertion, policy):
        self._verifier.validate_current(self._provider_key, assertion)
        principal = next((p for p in policy._principals.values()
                          if (p.issuer, p.subject) == (assertion.issuer, assertion.subject)), None)
        if principal is None or not principal.active:
            raise AuthenticationError()
        return principal.id

    def _dispatch(self, environ):
        assertion = self._verify_request(environ)
        method, args, kwargs, write, shape = self._command(environ)

        def operation(policy, actor):
            result = getattr(policy, method)(actor, *args, **kwargs)
            if shape == "page":
                total, records = result
                return {"total": total, "items": [asdict(item) for item in records]}
            if shape == "list":
                return [asdict(item) for item in result]
            return asdict(result)

        if method == "get_module_status":
            # Only this new read completes its ordinary final-current guards on
            # policy denial. Unwrap FIRST so W05's replacing rejection is also
            # inside capture; never infer identity/scope from the failed hint.
            assertion, operation = _workspace_hint_operation(assertion, operation)
            pending, denied = None, object()  # Request-local, never serialized.

            def observed(policy, actor):
                nonlocal pending
                try:
                    return operation(policy, actor)
                except PolicyError as error:
                    pending = error
                    return denied

            result = self._authenticated_transaction(assertion, observed, write=False)
            # Authentication/storage/fatal exceptions above win. Only a normal
            # completed read can reach this rethrow, after Cookie clock commit.
            if pending is not None:
                raise pending
            return result
        return self._authenticated_transaction(assertion, operation, write=write)

    @staticmethod
    def _body(environ, keys):
        if environ.get("CONTENT_TYPE", "").lower() not in {"application/json", "application/json; charset=utf-8"}:
            raise PolicyError(415, "json_required")
        length = environ.get("CONTENT_LENGTH", "")
        if type(length) is not str or not re.fullmatch(r"[0-9]{1,6}", length):
            raise PolicyError(411, "length_required")
        size = int(length)
        if size > _MAX_BODY:
            raise PolicyError(413, "body_too_large")
        try:
            raw = environ["wsgi.input"].read(size)
            if type(raw) is not bytes or len(raw) != size:
                raise ValueError("truncated body")
            body = json.loads(raw.decode("utf-8"), object_pairs_hook=_object,
                              parse_constant=lambda _: (_ for _ in ()).throw(ValueError()))
        except (KeyError, ValueError, UnicodeError, RecursionError):
            raise _invalid() from None
        if type(body) is not dict or set(body) != keys:
            raise _invalid()
        for value in body.values():
            if type(value) is not str:
                raise _invalid()
            try:
                value.encode("utf-8")
            except UnicodeError:
                raise _invalid() from None
        return body

    @staticmethod
    def _revision(environ):
        value = environ.get("HTTP_IF_MATCH")
        if value is None:
            raise PolicyError(428, "precondition_required")
        match = _REVISION.fullmatch(value) if type(value) is str else None
        if match is None or int(match[1]) > MAX_REVISION:
            raise PolicyError(400, "invalid_revision")
        return int(match[1])

    @staticmethod
    def _page(query):
        try:
            pairs = parse_qsl(query, keep_blank_values=True, strict_parsing=True, max_num_fields=2)
            values = _object(pairs)
            if not values.keys() <= {"offset", "limit"}:
                raise ValueError()
            if any(not re.fullmatch(r"[0-9]{1,8}", value) for value in values.values()):
                raise ValueError()
            return {key: int(value) for key, value in values.items()}
        except ValueError:
            raise _invalid() from None

    def _command(self, environ):
        # Explicit routes and argument names, never a client-supplied method name.
        path, verb = environ.get("PATH_INFO", ""), environ.get("REQUEST_METHOD", "")
        query = environ.get("QUERY_STRING", "")
        if type(path) is not str or len(path) > 1000 or type(query) is not str or len(query) > 256:
            raise _invalid()
        parts = path.split("/")
        if parts[:3] != ["", "api", "workspaces"] or any(not _SEGMENT.fullmatch(p) for p in parts[3:]):
            raise PolicyError(404, "not_found")
        if len(parts) == 3 and verb == "GET" and not query:
            return "list_workspaces", (), {}, False, "list"
        if len(parts) == 5 and parts[4] == "members" and verb == "GET" and not query:
            return "list_memberships", (parts[3],), {}, False, "list"
        if len(parts) == 6 and parts[4] == "members" and verb == "PATCH" and not query:
            body = self._body(environ, {"role", "state"})
            body["expected_revision"] = self._revision(environ)
            return "set_membership", (parts[3], parts[5]), body, True, "record"
        if len(parts) == 6 and parts[4] == "modules" and verb == "GET":
            if query:
                raise _invalid()
            return "get_module_status", (parts[3], parts[5]), {}, False, "record"
        if len(parts) >= 7 and parts[4] == "modules" and parts[6] == "resources":
            if len(parts) == 7 and verb == "GET":
                return "list_resources", (parts[3], parts[5]), self._page(query), False, "page"
            if len(parts) == 8 and not query:
                args = (parts[3], parts[5], parts[7])
                if verb == "GET":
                    return "read_resource", args, {}, False, "record"
                if verb == "PUT":
                    body = self._body(environ, {"text"})
                    body["expected_revision"] = self._revision(environ)
                    return "update_resource", args, body, True, "record"
        raise PolicyError(404, "not_found")
