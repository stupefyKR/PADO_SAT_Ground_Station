import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
from check_secrets import inspect_blob  # noqa: E402


class SecretCheckTests(unittest.TestCase):
    def test_rejects_private_key_path(self):
        self.assertIn("secret file path", inspect_blob("keys/signing.key", b"x"))

    def test_rejects_private_key_block(self):
        block = b"-----BEGIN " + b"PRIVATE KEY-----\ndata\n"
        self.assertIn("private key block", inspect_blob("notes.txt", block))

    def test_rejects_credential_assignment(self):
        assignment = b"password" + b" = example-secret-value\n"
        self.assertIn("credential assignment", inspect_blob("settings.json", assignment))

    def test_allows_reference_text(self):
        self.assertEqual([], inspect_blob("docs/README.md", b"Keep secrets out of Git."))


if __name__ == "__main__":
    unittest.main()
