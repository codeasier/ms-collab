# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Bounded ACP v1 protocol subset, WITHOUT a transport or execution authority.

Every returned byte is an in-memory protocol value, never a dispatch receipt.
The caller must not connect this model to an agent before separately implementing
verified OS isolation, current per-exchange authorization and safe publication.
No callback, filesystem, terminal, authentication, MCP, restore or replay exists.
"""
from dataclasses import dataclass
import json
import math

MAX_FRAME = 65536
MAX_DEPTH = 24
MAX_NODES = 4096
MAX_MESSAGES = 256
MAX_TOTAL = 1024 * 1024
MAX_TEXT = 8192
MAX_PROMPT = 16000
SAFE_INTEGER = 2**53 - 1


class ProtocolError(ValueError):
    """Fixed codes only; neither peer data nor JSON parser errors are exposed."""

    def __init__(self, code="invalid_frame"):
        if code not in {"invalid_frame", "protocol_closed", "invalid_transition",
                        "unsupported_version", "authentication_required",
                        "peer_error", "exchange_limit", "invalid_message"}:
            code = "invalid_message"
        self.code = code
        super().__init__(code)


def _bad():
    raise ValueError()


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            _bad()
        result[key] = value
    return result


def _string(value, limit, *, empty=False):
    if (type(value) is not str or (not empty and not value.strip())
            or "\x00" in value or len(value.encode("utf-8")) > limit):
        _bad()
    return value


def _bounded(value):
    pending, count = [(value, 0)], 0
    while pending:
        item, depth = pending.pop()
        count += 1
        if count > MAX_NODES or depth > MAX_DEPTH:
            _bad()
        if type(item) is dict:
            for key, child in item.items():
                _string(key, MAX_FRAME, empty=True)
                pending.append((child, depth + 1))
        elif type(item) is list:
            pending.extend((child, depth + 1) for child in item)
        elif type(item) is str:
            _string(item, MAX_FRAME, empty=True)
        elif type(item) is int:
            if abs(item) > SAFE_INTEGER:
                _bad()
        elif type(item) is float:
            if not math.isfinite(item):
                _bad()
        elif item is not None and type(item) is not bool:
            _bad()


def decode_frame(frame):
    """Decode exactly one LF-terminated JSON object, with no batches or extras."""
    try:
        if (type(frame) is not bytes or not 2 <= len(frame) <= MAX_FRAME
                or not frame.endswith(b"\n") or b"\n" in frame[:-1]):
            _bad()
        value = json.loads(frame[:-1].decode("utf-8"), object_pairs_hook=_pairs,
                           parse_constant=lambda _: _bad())
        if type(value) is not dict:
            _bad()
        _bounded(value)
        return value
    except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
        raise ProtocolError("invalid_frame") from None


def _encode(value):
    payload = json.dumps(value, ensure_ascii=False, separators=(",", ":"),
                         allow_nan=False).encode("utf-8") + b"\n"
    if len(payload) > MAX_FRAME:
        _bad()
    return payload


class FrameDecoder:
    """Incremental bounded framing only. No reads, writes or callbacks occur."""

    def __init__(self):
        self._pending = b""
        self._closed = False

    def feed(self, chunk):
        if self._closed:
            raise ProtocolError("protocol_closed")
        try:
            if type(chunk) is not bytes or len(chunk) > MAX_FRAME:
                _bad()
            pending = self._pending + chunk
            frames = []
            while b"\n" in pending:
                end = pending.index(b"\n") + 1
                frame, pending = pending[:end], pending[end:]
                decode_frame(frame)
                frames.append(frame)
            if len(pending) >= MAX_FRAME:
                _bad()
            self._pending = pending
            return tuple(frames)
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self._closed, self._pending = True, b""
            raise ProtocolError("invalid_frame") from None

    def finish(self):
        truncated = bool(self._pending)
        self._closed, self._pending = True, b""
        if truncated:
            raise ProtocolError("invalid_frame")


@dataclass(frozen=True)
class TextEvent:
    """Explicitly user-addressed source text, NOT sanitized publication content."""
    kind: str
    text: str


@dataclass(frozen=True)
class Receipt:
    """A pure reduction result. 'outbound' bytes have NOT been sent anywhere."""
    outbound: tuple[bytes, ...] = ()
    events: tuple[TextEvent, ...] = ()


def _fields(value, required, optional=()):
    if type(value) is not dict or not set(required) <= set(value) or set(value) - set(required) - set(optional):
        _bad()


def _rpc_id(value):
    if type(value) is int and 0 <= value <= SAFE_INTEGER:
        return value
    if type(value) is str:
        return _string(value, 128)
    _bad()


class Protocol:
    """One initialize/new/text-prompt exchange; no reconnect or second turn.

    This class has no credentials, scope grant, policy callback, process handle,
    network, stream or persistence. It MUST NOT be treated as a runnable adapter.
    Returned text is only source selection; redaction and authorization are still
    required before publication or history ingestion. Raw peer content is not kept.
    """

    def __init__(self):
        self._state = "new"
        self._session = None
        self._pending = None
        self._messages = self._bytes = self._text_bytes = 0

    @property
    def state(self):
        return self._state

    def _terminal(self, state):
        self._state, self._session, self._pending = state, None, None

    def close(self):
        self._terminal("closed")

    def _require(self, state):
        if self._state in {"closed", "failed", "completed", "cancelled"}:
            raise ProtocolError("protocol_closed")
        if self._state != state:
            self.close()
            raise ProtocolError("invalid_transition")

    def _request(self, identity, method, params, state):
        self._pending, self._state = identity, state
        return _encode({"jsonrpc": "2.0", "id": identity, "method": method, "params": params})

    def start(self):
        self._require("new")
        return self._request(1, "initialize", {"protocolVersion": 1,
            "clientCapabilities": {}, "clientInfo": {"name": "ms-collab-protocol", "version": "1"}}, "initializing")

    def new_session(self):
        self._require("initialized")
        return self._request(2, "session/new", {"cwd": "/workspace", "mcpServers": []}, "creating")

    def prompt(self, text):
        self._require("ready")
        try:
            _string(text, MAX_PROMPT)
            return self._request(3, "session/prompt", {"sessionId": self._session,
                "prompt": [{"type": "text", "text": text}]}, "prompting")
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError):
            self.close()
            raise ProtocolError("invalid_message") from None

    def cancel(self):
        self._require("prompting")
        result = _encode({"jsonrpc": "2.0", "method": "session/cancel",
                          "params": {"sessionId": self._session}})
        # Local cancellation is not proof the agent stopped. Ignore no further
        # traffic: refuse it, clear the locator, and require separate transport stop.
        self._terminal("cancelled")
        return result

    def receive(self, frame):
        if self._state in {"new", "closed", "failed", "completed", "cancelled"}:
            self.close()
            raise ProtocolError("protocol_closed")
        try:
            value = decode_frame(frame)
            self._messages += 1
            self._bytes += len(frame)
            if self._messages > MAX_MESSAGES or self._bytes > MAX_TOTAL:
                raise ProtocolError("exchange_limit")
            if value.get("jsonrpc") != "2.0":
                _bad()
            if "method" in value:
                return self._peer_method(value)
            _fields(value, ("jsonrpc", "id"), ("result", "error"))
            if (type(value["id"]) is not int or value["id"] != self._pending
                    or ("result" in value) == ("error" in value)):
                _bad()
            if "error" in value:
                raise ProtocolError("peer_error")
            result = value["result"]
            if self._state == "initializing":
                _fields(result, ("protocolVersion", "agentCapabilities"), ("agentInfo", "authMethods", "_meta"))
                if type(result["protocolVersion"]) is not int or result["protocolVersion"] != 1:
                    raise ProtocolError("unsupported_version")
                if type(result["agentCapabilities"]) is not dict or type(result.get("authMethods", [])) is not list:
                    _bad()
                if result.get("authMethods"):
                    raise ProtocolError("authentication_required")
                self._state = "initialized"
            elif self._state == "creating":
                _fields(result, ("sessionId",), ("modes", "models", "configOptions", "_meta"))
                session = _string(result["sessionId"], 512)
                if any(ord(character) < 32 or ord(character) == 127 for character in session):
                    _bad()
                self._session, self._state = session, "ready"
            elif self._state == "prompting":
                _fields(result, ("stopReason",), ("usage", "_meta"))
                if result["stopReason"] not in {"end_turn", "max_tokens", "max_turn_requests", "refusal", "cancelled"}:
                    _bad()
                state = {"end_turn": "completed", "cancelled": "cancelled"}.get(result["stopReason"], "failed")
                self._terminal(state)
            else:
                _bad()
            self._pending = None
            return Receipt()
        except ProtocolError:
            self.close()
            raise
        except (ValueError, TypeError, UnicodeError, RecursionError, OverflowError, KeyError):
            self.close()
            raise ProtocolError("invalid_message") from None

    def _peer_method(self, value):
        _fields(value, ("jsonrpc", "method", "params"), ("id",))
        method = _string(value["method"], 128)
        params = value["params"]
        if type(params) is not dict:
            _bad()
        if "id" in value:
            identity = _rpc_id(value["id"])
            if method == "session/request_permission":
                if self._state != "prompting" or params.get("sessionId") != self._session:
                    _bad()
                return Receipt(outbound=(_encode({"jsonrpc": "2.0", "id": identity,
                    "result": {"outcome": {"outcome": "cancelled"}}}),))
            return Receipt(outbound=(_encode({"jsonrpc": "2.0", "id": identity,
                "error": {"code": -32601, "message": "Method not found"}}),))
        if method != "session/update":
            return Receipt()  # Unimplemented notifications never execute or display.
        _fields(params, ("sessionId", "update"))
        if self._state != "prompting" or params["sessionId"] != self._session:
            _bad()
        update = params["update"]
        if type(update) is not dict:
            _bad()
        kind = update.get("sessionUpdate")
        if kind not in {"agent_message_chunk", "agent_thought_chunk"}:
            return Receipt()  # No tool/plan/raw object projection in this subset.
        _fields(update, ("sessionUpdate", "content"), ("messageId", "_meta"))
        content = update["content"]
        if type(content) is not dict or content.get("type") != "text":
            return Receipt()
        _fields(content, ("type", "text"), ("annotations", "_meta"))
        text = _string(content["text"], MAX_TEXT, empty=True)
        annotations = content.get("annotations")
        if annotations is None:
            return Receipt()
        if type(annotations) is not dict:
            _bad()
        audience = annotations.get("audience")
        if audience is None:
            return Receipt()
        if (type(audience) is not list or not audience or len(audience) > 2
                or any(type(item) is not str or item not in {"user", "assistant"} for item in audience)
                or len(set(audience)) != len(audience)):
            _bad()
        if "user" not in audience:
            return Receipt()
        self._text_bytes += len(text.encode("utf-8"))
        if self._text_bytes > MAX_FRAME:
            raise ProtocolError("exchange_limit")
        return Receipt(events=(TextEvent("assistant_message" if kind == "agent_message_chunk"
                                         else "user_visible_report", text),))
