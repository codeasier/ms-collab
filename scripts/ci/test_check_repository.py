"""Regression tests use synthetic strings, never real credentials."""
import subprocess
import tempfile
import unittest
from pathlib import Path

from check_repository import check, inspect_file


class PublicContentTests(unittest.TestCase):
    def test_clean_source(self):
        self.assertEqual(inspect_file("app/example.py", b"print('demo')\n"), [])

    def test_only_exact_environment_templates_are_allowed(self):
        for name in (".env.example", "nested/.env.template"):
            self.assertEqual(inspect_file(name, b"TOKEN=\n"), [])
        for name in (".env", ".env.local", ".env.example.backup", "nested/.env.production"):
            self.assertTrue(inspect_file(name, b"TOKEN=\n"), name)

    def test_template_contents_are_still_checked(self):
        fake = ("gh" + "p_" + "a" * 36).encode()
        self.assertTrue(inspect_file(".env.example", fake))

    def test_databases_and_sidecars(self):
        for name in ("state.db", "state.db-wal", "nested/state.sqlite3-shm", "cache.sqlite-journal"):
            self.assertTrue(inspect_file(name, b""), name)

    def test_private_keys_and_generated_files(self):
        for name in ("a.key", "cert.p12", "id_ed25519", "logs/run.txt", "node_modules/a.js"):
            self.assertTrue(inspect_file(name, b""), name)
        marker = ("-----BEGIN " + "OPENSSH PRIVATE KEY-----").encode()
        self.assertTrue(inspect_file("innocent.txt", marker))

    def test_no_secret_value_in_diagnostics(self):
        fake = ("github_" + "pat_" + "a" * 50).encode()
        result = str(inspect_file("source.txt", fake))
        self.assertTrue(result)
        self.assertNotIn(fake.decode(), result)

    def test_only_tracked_files_are_checked_and_links_not_followed(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            subprocess.run(["git", "init", "-q", directory], check=True)
            (root / "README.md").write_text("synthetic\n")
            (root / ".env").write_text("local-only\n")
            subprocess.run(["git", "add", "README.md"], cwd=root, check=True)
            self.assertEqual(check(root), [])
            (root / "outside").symlink_to("/does-not-exist")
            subprocess.run(["git", "add", "outside"], cwd=root, check=True)
            errors = check(root)
            self.assertEqual(len(errors), 1)
            self.assertIn("symlink", errors[0][1])


if __name__ == "__main__":
    unittest.main()
