# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Pure in-memory synthetic ACP transcripts. No subprocess, provider or transport."""
import ast
import inspect
import json
import unittest

from packages.assistant_protocol import protocol
from packages.assistant_protocol.protocol import (
    FrameDecoder, MAX_FRAME, MAX_MESSAGES, MAX_PROMPT, MAX_TEXT,
    Protocol, ProtocolError, Receipt, TextEvent, decode_frame,
)


def frame(value):
    return json.dumps(value, ensure_ascii=False, separators=(",", ":")).encode() + b"\n"


def response(identity, result):
    return frame({"jsonrpc": "2.0", "id": identity, "result": result})


def initialized():
    peer = Protocol()
    peer.start()
    peer.receive(response(1, {"protocolVersion": 1, "agentCapabilities": {}, "authMethods": []}))
    return peer


def ready():
    peer = initialized()
    peer.new_session()
    peer.receive(response(2, {"sessionId": "opaque-private-session"}))
    return peer


def prompting():
    peer = ready()
    peer.prompt("A synthetic user request")
    return peer


def update(text="Synthetic literal <script>text</script>", audience=("user",), kind="agent_message_chunk", session="opaque-private-session"):
    content = {"type": "text", "text": text}
    if audience is not None:
        content["annotations"] = {"audience": list(audience)}
    return frame({"jsonrpc": "2.0", "method": "session/update", "params": {
        "sessionId": session, "update": {"sessionUpdate": kind, "content": content}}})


class FramingTests(unittest.TestCase):
    def test_fragmented_and_multiple_complete_frames(self):
        decoder = FrameDecoder()
        value = response(1, {})
        self.assertEqual(decoder.feed(value[:5]), ())
        self.assertEqual(decoder.feed(value[5:] + value), (value, value))
        self.assertIsNone(decoder.finish())
        with self.assertRaisesRegex(ProtocolError, "protocol_closed"):
            decoder.feed(value)

    def test_utf8_can_be_split_between_chunks(self):
        decoder = FrameDecoder()
        value = frame({"text": "安全"})
        point = value.index("安".encode()) + 1
        self.assertEqual(decoder.feed(value[:point]), ())
        self.assertEqual(decoder.feed(value[point:]), (value,))

    def test_frame_requires_one_object_and_newline(self):
        for value in (b"{}", b"\n", b"[]\n", b"null\n", b"{}\n{}\n", b"{}garbage\n", b"\xef\xbb\xbf{}\n", "{}\n", bytearray(b"{}\n")):
            with self.subTest(value=type(value).__name__), self.assertRaisesRegex(ProtocolError, "invalid_frame"):
                decode_frame(value)
        self.assertEqual(decode_frame(b"{}\r\n"), {})

    def test_duplicate_nonfinite_unsafe_integer_invalid_utf8_rejected(self):
        for value in (b'{"a":1,"a":2}\n', b'{"x":{"a":1,"a":2}}\n',
                      b'{"x":NaN}\n', b'{"x":Infinity}\n', b'{"x":1e999}\n',
                      b'{"x":9007199254740992}\n', b'{"x":"\xff"}\n',
                      b'{"x":"\\ud800"}\n', b'{"x":"\\u0000"}\n'):
            with self.subTest(value=value), self.assertRaisesRegex(ProtocolError, "invalid_frame"):
                decode_frame(value)

    def test_depth_nodes_and_frame_size_are_bounded(self):
        nested = 1
        for _ in range(26):
            nested = [nested]
        for value in (frame({"x": nested}), frame({"x": [0] * 4096}), frame({"x": "x" * MAX_FRAME})):
            with self.assertRaisesRegex(ProtocolError, "invalid_frame"):
                decode_frame(value)

    def test_decoder_failure_clears_pending_and_is_permanent(self):
        decoder = FrameDecoder()
        decoder.feed(b'{"a":')
        with self.assertRaisesRegex(ProtocolError, "invalid_frame"):
            decoder.feed(b"NaN}\n")
        self.assertEqual(decoder._pending, b"")
        with self.assertRaisesRegex(ProtocolError, "protocol_closed"):
            decoder.feed(b"{}\n")

    def test_unterminated_bound_and_truncated_eof(self):
        decoder = FrameDecoder()
        with self.assertRaisesRegex(ProtocolError, "invalid_frame"):
            decoder.feed(b" " * MAX_FRAME)
        decoder = FrameDecoder()
        decoder.feed(b"{")
        with self.assertRaisesRegex(ProtocolError, "invalid_frame"):
            decoder.finish()
        self.assertEqual(decoder._pending, b"")


class ExchangeTests(unittest.TestCase):
    def test_exact_minimal_requests_have_no_ambient_authority(self):
        peer = Protocol()
        hello = decode_frame(peer.start())
        self.assertEqual(hello, {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {
            "protocolVersion": 1, "clientCapabilities": {},
            "clientInfo": {"name": "ms-collab-protocol", "version": "1"}}})
        peer.receive(response(1, {"protocolVersion": 1, "agentCapabilities": {"loadSession": True}, "authMethods": []}))
        self.assertEqual(decode_frame(peer.new_session()), {"jsonrpc": "2.0", "id": 2,
            "method": "session/new", "params": {"cwd": "/workspace", "mcpServers": []}})
        peer.receive(response(2, {"sessionId": "opaque-private-session"}))
        self.assertEqual(decode_frame(peer.prompt("latest text")), {"jsonrpc": "2.0", "id": 3,
            "method": "session/prompt", "params": {"sessionId": "opaque-private-session",
                "prompt": [{"type": "text", "text": "latest text"}]}})
        self.assertNotIn("opaque-private-session", repr(peer))

    def test_matching_completion_is_single_turn_and_clears_locator(self):
        peer = prompting()
        self.assertEqual(peer.receive(response(3, {"stopReason": "end_turn"})), Receipt())
        self.assertEqual(peer.state, "completed")
        self.assertIsNone(peer._session)
        self.assertIsNone(peer._pending)
        for method in (peer.start, peer.new_session, lambda: peer.prompt("again"), peer.cancel):
            with self.assertRaisesRegex(ProtocolError, "protocol_closed"):
                method()

    def test_early_call_closes_without_retry(self):
        for factory, method in ((Protocol, "new_session"), (initialized, "start"), (prompting, "new_session")):
            peer = factory()
            with self.assertRaisesRegex(ProtocolError, "invalid_transition"):
                getattr(peer, method)()
            self.assertEqual(peer.state, "closed")

    def test_wrong_response_id_and_bool_id_fail_closed(self):
        for identity in (0, 2, True, 1.0, "1", None):
            peer = Protocol()
            peer.start()
            with self.assertRaisesRegex(ProtocolError, "invalid_message"):
                peer.receive(response(identity, {"protocolVersion": 1, "agentCapabilities": {}}))
            self.assertEqual(peer.state, "closed")

    def test_duplicate_response_is_not_accepted(self):
        peer = initialized()
        with self.assertRaisesRegex(ProtocolError, "invalid_message"):
            peer.receive(response(1, {"protocolVersion": 1, "agentCapabilities": {}}))
        self.assertEqual(peer.state, "closed")

    def test_version_and_auth_requirements_are_not_worked_around(self):
        for result, code in (({"protocolVersion": 2, "agentCapabilities": {}}, "unsupported_version"),
                             ({"protocolVersion": True, "agentCapabilities": {}}, "unsupported_version"),
                             ({"protocolVersion": 1, "agentCapabilities": {}, "authMethods": [{"id": "secret-provider"}]}, "authentication_required")):
            peer = Protocol()
            peer.start()
            with self.assertRaisesRegex(ProtocolError, code):
                peer.receive(response(1, result))
            self.assertEqual(peer.state, "closed")

    def test_peer_errors_have_no_peer_data_in_exception(self):
        peer = prompting()
        with self.assertRaises(ProtocolError) as error:
            peer.receive(frame({"jsonrpc": "2.0", "id": 3, "error": {
                "code": -32000, "message": "private peer detail", "data": "secret"}}))
        self.assertEqual(str(error.exception), "peer_error")
        self.assertIsNone(peer._session)

    def test_conflicting_or_unknown_envelopes_rejected(self):
        for extra in ({"result": {}, "error": {}}, {"result": {}, "owner": "victim"}, {}):
            peer = prompting()
            with self.assertRaisesRegex(ProtocolError, "invalid_message"):
                peer.receive(frame({"jsonrpc": "2.0", "id": 3, **extra}))

    def test_session_identifier_and_prompt_bounds(self):
        for session in ("", "x" * 513, "bad\n", "bad\x7f", 1):
            peer = initialized()
            peer.new_session()
            with self.assertRaisesRegex(ProtocolError, "invalid_message"):
                peer.receive(response(2, {"sessionId": session}))
        for prompt in ("", " ", "x" * (MAX_PROMPT + 1), "\ud800", ["text"], {"command": "noop"}):
            peer = ready()
            with self.assertRaisesRegex(ProtocolError, "invalid_message"):
                peer.prompt(prompt)
            self.assertEqual(peer.state, "closed")

    def test_permission_requests_never_select_an_approval(self):
        peer = prompting()
        result = peer.receive(frame({"jsonrpc": "2.0", "id": "agent-request-1",
            "method": "session/request_permission", "params": {
                "sessionId": "opaque-private-session", "toolCall": {"rawInput": "not displayed"},
                "options": [{"kind": "allow_always", "optionId": "allow"}]}}))
        self.assertEqual(decode_frame(result.outbound[0]), {"jsonrpc": "2.0", "id": "agent-request-1",
            "result": {"outcome": {"outcome": "cancelled"}}})
        self.assertEqual(result.events, ())
        self.assertEqual(peer.state, "prompting")

    def test_cross_session_permission_or_notification_fails(self):
        for message in (update(session="other"), frame({"jsonrpc": "2.0", "id": 8,
                "method": "session/request_permission", "params": {"sessionId": "other"}})):
            peer = prompting()
            with self.assertRaisesRegex(ProtocolError, "invalid_message"):
                peer.receive(message)
            self.assertIsNone(peer._session)

    def test_filesystem_terminal_auth_and_unknown_requests_only_return_error(self):
        peer = prompting()
        for method in ("fs/read_text_file", "fs/write_text_file", "terminal/create", "terminal/output",
                       "terminal/kill", "authenticate", "elicitation/create", "_private/execute"):
            receipt = peer.receive(frame({"jsonrpc": "2.0", "id": 90, "method": method,
                "params": {"path": "/private", "command": "never run", "sessionId": "other"}}))
            self.assertEqual(decode_frame(receipt.outbound[0]), {"jsonrpc": "2.0", "id": 90,
                "error": {"code": -32601, "message": "Method not found"}})
            self.assertEqual(receipt.events, ())

    def test_explicit_user_text_only_and_no_raw_content_retention(self):
        peer = prompting()
        text = "Synthetic literal <script>text</script>"
        self.assertEqual(peer.receive(update()).events, (TextEvent("assistant_message", text),))
        self.assertEqual(peer.receive(update(kind="agent_thought_chunk", audience=("user", "assistant"))).events,
                         (TextEvent("user_visible_report", text),))
        for audience in (None, ("assistant",)):
            self.assertEqual(peer.receive(update(audience=audience)), Receipt())
        self.assertNotIn(text, repr(vars(peer)))

    def test_unknown_tool_media_and_notification_payloads_are_not_displayed(self):
        peer = prompting()
        for kind in ("tool_call", "tool_call_update", "plan", "unknown"):
            message = frame({"jsonrpc": "2.0", "method": "session/update", "params": {
                "sessionId": "opaque-private-session", "update": {"sessionUpdate": kind, "rawOutput": "secret"}}})
            self.assertEqual(peer.receive(message), Receipt())
        message = frame({"jsonrpc": "2.0", "method": "session/update", "params": {
            "sessionId": "opaque-private-session", "update": {"sessionUpdate": "agent_message_chunk",
                "content": {"type": "resource", "resource": {"uri": "file:///private"}}}}})
        self.assertEqual(peer.receive(message), Receipt())
        self.assertEqual(peer.receive(frame({"jsonrpc": "2.0", "method": "_unknown/notice", "params": {"text": "secret"}})), Receipt())

    def test_text_byte_audience_and_total_display_bounds(self):
        for message in (update(text="x" * (MAX_TEXT + 1)), update(audience=("user", "other")),
                        update(audience=("user", "user")), update(audience=())):
            with self.assertRaises(ProtocolError):
                prompting().receive(message)
        peer = prompting()
        for _ in range(8):
            peer.receive(update(text="x" * MAX_TEXT))
        with self.assertRaisesRegex(ProtocolError, "exchange_limit"):
            peer.receive(update(text="x"))

    def test_all_incoming_messages_count_even_hidden_or_unknown(self):
        peer = prompting()
        quiet = frame({"jsonrpc": "2.0", "method": "_ignored", "params": {}})
        # Two responses were already received during setup.
        for _ in range(MAX_MESSAGES - 2):
            peer.receive(quiet)
        with self.assertRaisesRegex(ProtocolError, "exchange_limit"):
            peer.receive(quiet)

    def test_total_wire_bytes_count_even_discarded_metadata(self):
        peer = prompting()
        quiet = frame({"jsonrpc": "2.0", "method": "_ignored", "params": {"data": "x" * 60000}})
        for _ in range(17):
            peer.receive(quiet)
        with self.assertRaisesRegex(ProtocolError, "exchange_limit"):
            peer.receive(quiet)

    def test_cancel_is_terminal_not_proof_of_process_stop(self):
        peer = prompting()
        self.assertEqual(decode_frame(peer.cancel()), {"jsonrpc": "2.0", "method": "session/cancel",
            "params": {"sessionId": "opaque-private-session"}})
        self.assertEqual(peer.state, "cancelled")
        self.assertIsNone(peer._session)
        with self.assertRaisesRegex(ProtocolError, "protocol_closed"):
            peer.receive(update())

    def test_non_success_stop_reasons_are_not_reported_as_completed(self):
        for reason, state in (("max_tokens", "failed"), ("max_turn_requests", "failed"),
                              ("refusal", "failed"), ("cancelled", "cancelled")):
            peer = prompting()
            peer.receive(response(3, {"stopReason": reason}))
            self.assertEqual(peer.state, state)
        with self.assertRaisesRegex(ProtocolError, "invalid_message"):
            prompting().receive(response(3, {"stopReason": "success"}))

    def test_no_execution_or_transport_imports(self):
        tree = ast.parse(inspect.getsource(protocol))
        imports = {node.module for node in ast.walk(tree) if isinstance(node, ast.ImportFrom)}
        imports.update(alias.name for node in ast.walk(tree) if isinstance(node, ast.Import) for alias in node.names)
        self.assertEqual(imports, {"dataclasses", "json", "math"})
        self.assertFalse(any(isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
                             and node.func.id in {"open", "eval", "exec", "compile", "__import__"}
                             for node in ast.walk(tree)))


if __name__ == "__main__":
    unittest.main()
