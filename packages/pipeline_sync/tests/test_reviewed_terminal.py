# Copyright (c) 2026 codeasier
# SPDX-License-Identifier: MIT
"""Synthetic Linux integration. Never print, persist or attach a TTY transcript."""
from contextlib import contextmanager
import errno
import json
import os
from pathlib import Path
import re
import select
import subprocess
import sys
import time
from unittest import TestCase

from packages.pipeline_api import PipelineApplication
from packages.pipeline_sync import Connection, PipelineReviewPolicy, ProviderError
from packages.pipeline_sync.tests._review_fixture import (
    AUDIENCE, ISSUER, LABEL, NOW, OLD_IDS, ORIGIN, SCOPE, TOKEN, USER_TOKEN, ReviewedFixture, providers, setup_gate,
)
from packages.workspaces import SQLiteWorkspaceService


PROMPT = b"Type PUBLISH followed by one space and this digest, then Enter: "
CANDIDATE = b"Candidate (formatted canonical JSON):\n"
ROOT = Path(__file__).resolve().parents[3]
MODULE = "packages.pipeline_sync.tests.test_reviewed_terminal"


def child(path, descriptor, realm):
    # POSIX session already created by Popen; no test runner console is used.
    if descriptor >= 0:
        import fcntl
        import termios
        fcntl.ioctl(descriptor, termios.TIOCSCTTY, 0)
        os.tcsetpgrp(descriptor, os.getpgrp())
    # Fixed synthetic setup, not a fresh grant derived from the caller or
    # current policy. Only the disposable realm differs between test stores.
    policy = PipelineReviewPolicy(connection=Connection(SCOPE, ORIGIN),
                                  reviewer_pin=(realm, "admin", ISSUER, "admin", 1),
                                  reviewer_epoch=1, membership_revision=1, audience=AUDIENCE)
    sync, args, fake = setup_gate(SQLiteWorkspaceService(path), policy=policy)
    args["expected_revision"] = 2
    try:
        result = sync.reviewed_refresh(**args)
    except ProviderError as exc:
        return {"review_not_confirmed": 29, "review_unavailable": 31}.get(str(exc), 80)
    return 0 if (result == {"revision": 3, "run_count": 1, "scope": SCOPE.as_dict(),
                             "full_history_complete": False, "live_provider_verified": False}
                 and fake.calls == ["/api/v1/pipelines/build/runs?limit=5",
                                    "/api/v1/pipelines/build/runs/r1"]) else 81


@contextmanager
def terminal(path, realm, *, attach=True, unsafe=None):
    master = slave = proc = None
    try:
        if attach:
            master, slave = os.openpty()
            os.fchmod(slave, 0o620 if unsafe=="group-writable" else 0o600)
            if unsafe in ("uppercase-output", "lowercase-input", "strip-input"):
                # Configure only the new disposable PTY, never the runner's.
                import termios
                flags = termios.tcgetattr(slave)
                if unsafe == "uppercase-output":
                    flags[1] |= termios.OPOST | termios.OLCUC
                elif unsafe == "lowercase-input":
                    flags[0] |= termios.IUCLC
                else:
                    flags[0] |= termios.ISTRIP
                termios.tcsetattr(slave, termios.TCSANOW, flags)
        env = {"PYTHONDONTWRITEBYTECODE":"1","PYTHONUTF8":"1"}
        if "LD_LIBRARY_PATH" in os.environ:  # Preserve the CI interpreter's own runtime library.
            env["LD_LIBRARY_PATH"] = os.environ["LD_LIBRARY_PATH"]
        proc = subprocess.Popen([sys.executable, "-m", MODULE, str(path), str(slave if attach else -1), realm],
                                cwd=ROOT, env=env, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                                stderr=subprocess.STDOUT, start_new_session=True,
                                pass_fds=(slave,) if attach else ())
        if attach:
            os.close(slave)
            slave = None
        yield master, proc
    finally:
        if proc is not None:
            if proc.poll() is None:
                proc.kill()
            proc.wait(timeout=10)
            proc.stdout.close()
        for fd in (master, slave):
            if fd is not None:
                os.close(fd)


def read_prompt(fd):
    """Bound time and bytes even if a child regresses. No private failure text."""
    buf = bytearray()
    deadline = time.monotonic()+15
    while len(buf) < 32768:
        remaining = deadline-time.monotonic()
        if remaining <= 0 or not select.select([fd], [], [], remaining)[0]:
            raise AssertionError("private review prompt deadline")
        try:
            chunk = os.read(fd, min(4096,32768-len(buf)))
        except OSError:
            raise AssertionError("private review terminal closed before decision") from None
        if not chunk:
            raise AssertionError("private review terminal EOF before decision")
        buf.extend(chunk)
        if buf.endswith(PROMPT):
            return bytes(buf).replace(b"\r\n", b"\n")
    raise AssertionError("private review display exceeded synthetic bound")


def finished(case, proc, code):
    case.assertEqual(proc.wait(timeout=15), code, "unexpected private review worker outcome")
    # Both standard streams use this one pipe. At EOF expect not even a byte;
    # never format any unexpected output into unittest's failure report.
    case.assertTrue(proc.stdout.read(1)==b"", "private worker leaked standard output")


class ReviewedTerminalTests(ReviewedFixture, TestCase):
    def setUp(self):
        super().setUp()
        self.seed_old()
        self.realm = self.args["review_policy"].reviewer_pin[0]
        self.app = PipelineApplication(store=SQLiteWorkspaceService(self.path), providers=providers(),
                                       provider_key="test", clock=lambda: NOW)

    def read(self, suffix="runs", identity="reader", expected=200):
        statuses = []
        body = b"".join(self.app({"REQUEST_METHOD":"GET", "PATH_INFO":"/api/workspaces/alpha/pipelines/ci/build/"+suffix,
                                  "QUERY_STRING":"", "HTTP_AUTHORIZATION":"Bearer "+identity},
                                 lambda status,_headers: statuses.append(int(status[:3]))))
        self.assertEqual(statuses, [expected])
        return json.loads(body).get("data")

    def assert_old(self):
        data = self.read()
        self.assertEqual((data["source"]["revision"], data["total"]), (2, 2))
        self.assertTrue(sorted(row["id"] for row in data["items"])==list(OLD_IDS), "reader saw a changed baseline")
        self.read("runs/r1", expected=404)
        self.assertEqual(len(self.footprint()[1]), 1)

    def checked_prompt(self, fd):
        screen = read_prompt(fd)
        self.assertTrue(all(32<=b<=126 or b==10 for b in screen), "terminal output must be plain ASCII")
        self.assertTrue(all(value not in screen for value in (TOKEN.encode(), USER_TOKEN.encode(), ISSUER.encode(), self.realm.encode(),
                             b"SYNTHETIC-EXCLUDED-FIELD", b"private_debug", b'"log"')), "non-projection in terminal output")
        lines = screen.splitlines()
        self.assertTrue(CANDIDATE in screen, "missing complete candidate section")
        self.assertTrue(b'Old run count: 2' in lines and b'New run count: 1' in lines and
                        b'Removed run IDs: ["old-a","old-b"]' in lines and b'Replace published revision: 2' in lines,
                        "wrong baseline, counts or removed IDs")
        self.assertTrue(b"Audience: "+AUDIENCE.encode() in lines and
                        b"current and future workspace members with pipeline read access" in screen,
                        "reader audience not explicit")
        self.assertTrue(b'Scope: {"connection_id":"ci","pipeline_id":"build","workspace_id":"alpha"}' in lines and
                        (b'Source connection: {"max_response_bytes":262144,"origin":"'+ORIGIN.encode()+
                         b'","run_limit":5,"timeout":10}') in lines, "wrong source or destination")
        block = screen.split(CANDIDATE,1)[1].split(b"\nDecision SHA-256: ",1)[0]
        self.assertTrue(b'\n  "scope": {' in block and b'\n  "runs": [' in block and
                        b'\n          "jobs": [' in block, "candidate lacks readable nested lines")
        payload = json.loads(block)
        candidate = json.dumps(payload, ensure_ascii=True, separators=(",",":")).encode("ascii")
        # Independent expectation of the full projection, not a substring or
        # a call to the real provider projection helper.
        job = {"id":"j1", "name":LABEL, "status":"success", "started_at":"2026-01-01T10:00:00+00:00",
               "finished_at":"2026-01-01T10:00:12+00:00"}
        row = {**SCOPE.as_dict(), "id":"r1", "observed_at":NOW.isoformat(), "status":"failure",
               "stages":[{"name":"Test","jobs":[job]}]}
        self.assertTrue(payload=={"version":1,"scope":SCOPE.as_dict(),"runs":[row]},
                        "operator did not see the complete normalized candidate")
        digests = re.findall(rb"^Decision SHA-256: ([0-9a-f]{64})$",screen,re.MULTILINE)
        self.assertEqual(len(digests),1, "missing unique decision challenge")
        return digests[0], candidate

    def test_real_private_pty_keeps_old_reader_view_until_exact_confirmation_then_serves_reviewed_bytes(self):
        with terminal(self.path, self.realm) as (master, proc):
            digest, candidate = self.checked_prompt(master)
            self.assertIsNone(proc.poll())
            self.assert_old()
            self.read(identity="other",expected=404)
            os.write(master, b"PUBLISH "+digest+b"\n")
            finished(self, proc, 0)
        data = self.read()
        self.assertEqual((data["source"]["revision"],data["total"]), (3,1))
        self.assertTrue([row["id"] for row in data["items"]]==["r1"], "new reader list does not match approved replacement")
        self.assertIs(data["source"]["full_history_complete"],False)
        self.assertIs(data["source"]["live_provider_verified"],False)
        self.assertTrue(self.read("runs/r1")["run"]==json.loads(candidate)["runs"][0], "reader detail differs from reviewed content")
        for removed in OLD_IDS:
            self.read("runs/"+removed,expected=404)
        self.read(identity="other",expected=404)
        record, audit = self.footprint()
        self.assertTrue(record.text.encode("ascii")==candidate, "persisted bytes differ from private screen")
        self.assertEqual([(e.actor_id,e.revision) for e in audit],[("admin",2),("admin",3)])

    def test_real_pty_rejects_generic_answer_and_previous_challenge_for_identical_unpublished_candidate(self):
        with terminal(self.path, self.realm) as (master,proc):
            previous, first = self.checked_prompt(master)
            os.write(master,b"yes\n")
            finished(self,proc,29)
        self.assert_old()
        with terminal(self.path, self.realm) as (master,proc):
            current, second = self.checked_prompt(master)
            self.assertTrue(first==second, "not the same candidate")
            self.assertTrue(current!=previous, "decision challenge was reused")
            os.write(master,b"PUBLISH "+previous+b"\n")
            finished(self,proc,29)
        self.assert_old()

    def test_shared_or_character_transforming_real_pty_receives_no_review_bytes_and_old_snapshot_survives(self):
        for unsafe in ("group-writable", "uppercase-output", "lowercase-input", "strip-input"):
            with self.subTest(mode=unsafe), terminal(self.path, self.realm, unsafe=unsafe) as (master,proc):
                finished(self,proc,31)
                # With all slave handles closed Linux returns EIO, or buffered
                # data first. An empty standard pipe alone would prove nothing.
                os.set_blocking(master,False)
                try:
                    leaked = os.read(master,1)
                except OSError as exc:
                    if exc.errno != errno.EIO:
                        raise
                    leaked = b""
                self.assertTrue(leaked==b"", "unsafe terminal received review bytes")
            self.assert_old()

    def test_no_controlling_terminal_never_publishes_or_emits_preview_to_standard_streams(self):
        with terminal(self.path, self.realm, attach=False) as (_,proc):
            finished(self,proc,31)
        self.assert_old()


if __name__ == "__main__":
    # No traceback/payload reaches either test logs or a file.
    try:
        code = child(sys.argv[1],int(sys.argv[2]),sys.argv[3])
    except Exception:
        code = 82
    sys.exit(code)
