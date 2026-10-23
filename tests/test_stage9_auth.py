import base64
import ctypes.util
import hashlib
import tempfile
import unittest
from pathlib import Path

from services.webapp.auth import AuthError, AuthStore, SodiumHasher, totp_at


class FakeHasher:
    def hash(self, password):
        return "$argon2id$test$" + hashlib.sha256(password.encode()).hexdigest()

    def verify(self, encoded, password):
        return encoded == self.hash(password)


class AuthTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.store = AuthStore(Path(self.temp.name) / "accounts.db", FakeHasher())

    def tearDown(self):
        self.store.close()
        self.temp.cleanup()

    def test_session_expiry_and_unknown_user(self):
        self.store.create_user("planner01", "planner", "valid-password-123")
        with self.assertRaises(AuthError):
            self.store.login("unknown", "valid-password-123", now=1000)
        session = self.store.login("planner01", "valid-password-123", now=1000)
        self.assertEqual(self.store.session(session.token, now=1001).role, "planner")
        self.assertIsNone(self.store.session(session.token, now=1001 + 1801))
        self.assertIsNone(self.store.session("invalid", now=1000))

    def test_lockout_and_recovery(self):
        self.store.create_user("operator01", "operator", "valid-password-123")
        for _ in range(5):
            with self.assertRaises(AuthError):
                self.store.login("operator01", "wrong", now=1000)
        with self.assertRaises(AuthError):
            self.store.login("operator01", "valid-password-123", now=1001)
        self.assertEqual(self.store.login("operator01", "valid-password-123",
                                          now=1901).role, "operator")

    def test_lead_totp_is_required_and_not_reusable(self):
        totp_seed = base64.b32encode(b"0123456789abcdefghij").decode()
        with self.assertRaises(AuthError):
            self.store.create_user("lead01", "lead", "valid-password-123")
        self.store.create_user("lead01", "lead", "valid-password-123", totp_seed)
        code = totp_at(totp_seed, 1000 // 30)
        with self.assertRaises(AuthError):
            self.store.login("lead01", "valid-password-123", now=1000)
        self.store.login("lead01", "valid-password-123", code, now=1000)
        with self.assertRaises(AuthError):
            self.store.login("lead01", "valid-password-123", code, now=1001)

    def test_absolute_expiry_and_logout(self):
        self.store.create_user("auditor01", "auditor", "valid-password-123")
        session = self.store.login("auditor01", "valid-password-123", now=1000)
        for at in range(1500, 1000 + 8 * 3600, 1500):
            self.assertIsNotNone(self.store.session(session.token, now=at))
        self.assertIsNone(self.store.session(session.token, now=1000 + 8 * 3600 + 1))
        second = self.store.login("auditor01", "valid-password-123", now=40000)
        self.store.logout(second.token)
        self.assertIsNone(self.store.session(second.token, now=40001))

    @unittest.skipUnless(ctypes.util.find_library("sodium"),
                         "libsodium not installed on this host")
    def test_system_argon2id(self):
        hasher = SodiumHasher()
        encoded = hasher.hash("valid-password-123")
        self.assertTrue(encoded.startswith("$argon2id$"))
        self.assertTrue(hasher.verify(encoded, "valid-password-123"))
        self.assertFalse(hasher.verify(encoded, "wrong"))


if __name__ == "__main__":
    unittest.main()
