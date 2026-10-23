"""Local portal accounts, Argon2id passwords, TOTP and bounded sessions."""

from __future__ import annotations

import base64
import ctypes
import ctypes.util
import hashlib
import hmac
import os
import re
import secrets
import sqlite3
import stat
import struct
import threading
import time
from dataclasses import dataclass
from pathlib import Path


ROLES = frozenset({"operator", "lead", "planner", "auditor"})
USER_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{2,63}\Z")
IDLE_SECONDS = 30 * 60
MAX_SECONDS = 8 * 60 * 60
LOCK_SECONDS = 15 * 60
MAX_FAILURES = 5


class AuthError(ValueError):
    pass


class SodiumHasher:
    """Use the system libsodium Argon2id password-string interface."""

    def __init__(self) -> None:
        name = ctypes.util.find_library("sodium")
        if not name:
            raise RuntimeError("libsodium is required for portal authentication")
        lib = ctypes.CDLL(name)
        lib.sodium_init.restype = ctypes.c_int
        lib.crypto_pwhash_strbytes.restype = ctypes.c_size_t
        lib.crypto_pwhash_opslimit_interactive.restype = ctypes.c_ulonglong
        lib.crypto_pwhash_memlimit_interactive.restype = ctypes.c_size_t
        lib.crypto_pwhash_str.argtypes = [ctypes.c_void_p, ctypes.c_char_p,
                                          ctypes.c_ulonglong, ctypes.c_ulonglong,
                                          ctypes.c_size_t]
        lib.crypto_pwhash_str.restype = ctypes.c_int
        lib.crypto_pwhash_str_verify.argtypes = [ctypes.c_char_p, ctypes.c_char_p,
                                                 ctypes.c_ulonglong]
        lib.crypto_pwhash_str_verify.restype = ctypes.c_int
        if lib.sodium_init() < 0:
            raise RuntimeError("libsodium initialization failed")
        self.lib = lib

    def hash(self, password: str) -> str:
        raw = password.encode("utf-8")
        if len(raw) > 1024:
            raise AuthError("password too long")
        output = ctypes.create_string_buffer(self.lib.crypto_pwhash_strbytes())
        result = self.lib.crypto_pwhash_str(
            output, raw, len(raw),
            self.lib.crypto_pwhash_opslimit_interactive(),
            self.lib.crypto_pwhash_memlimit_interactive())
        if result != 0:
            raise RuntimeError("password hashing failed")
        encoded = output.value.decode("ascii")
        if not encoded.startswith("$argon2id$"):
            raise RuntimeError("unexpected password algorithm")
        return encoded

    def verify(self, encoded: str, password: str) -> bool:
        raw = password.encode("utf-8")
        if len(raw) > 1024 or not encoded.startswith("$argon2id$"):
            return False
        return self.lib.crypto_pwhash_str_verify(
            encoded.encode("ascii"), raw, len(raw)) == 0


def totp_at(secret: str, counter: int) -> str:
    key = base64.b32decode(secret, casefold=True)
    digest = hmac.digest(key, struct.pack(">Q", counter), "sha1")
    offset = digest[-1] & 15
    value = struct.unpack(">I", digest[offset:offset + 4])[0] & 0x7fffffff
    return f"{value % 1_000_000:06d}"


def matching_totp_counter(secret: str, code: str, now: int,
                          last_counter: int) -> int | None:
    if not isinstance(code, str) or not re.fullmatch(r"[0-9]{6}", code):
        return None
    current = now // 30
    for counter in (current - 1, current, current + 1):
        if counter > last_counter and counter >= 0 and hmac.compare_digest(
                totp_at(secret, counter), code):
            return counter
    return None


@dataclass(frozen=True)
class Session:
    username: str
    role: str
    csrf: str
    token: str = ""


class AuthStore:
    def __init__(self, path: Path, hasher: object | None = None) -> None:
        if path.is_symlink():
            raise AuthError("account database cannot be a symlink")
        if path.exists() and os.name == "posix" and stat.S_IMODE(path.stat().st_mode) & 0o077:
            raise AuthError("account database must be private to its owner")
        if not path.exists():
            descriptor = os.open(path, os.O_CREAT | os.O_EXCL | os.O_RDWR, 0o600)
            os.close(descriptor)
        self.hasher = hasher if hasher is not None else SodiumHasher()
        self.db = sqlite3.connect(path, check_same_thread=False, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        self.lock = threading.RLock()
        with self.lock:
            self.db.executescript("""
                PRAGMA foreign_keys=ON;
                CREATE TABLE IF NOT EXISTS users (
                    username TEXT PRIMARY KEY, role TEXT NOT NULL,
                    password_hash TEXT NOT NULL, totp_secret TEXT,
                    last_totp INTEGER NOT NULL DEFAULT -1,
                    failures INTEGER NOT NULL DEFAULT 0,
                    locked_until INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, username TEXT NOT NULL,
                    csrf TEXT NOT NULL, created INTEGER NOT NULL,
                    last_seen INTEGER NOT NULL,
                    FOREIGN KEY(username) REFERENCES users(username) ON DELETE CASCADE
                );
            """)
        if os.name == "posix":
            os.chmod(path, 0o600)
        self.dummy_hash = self.hasher.hash(secrets.token_urlsafe(24))

    def close(self) -> None:
        with self.lock:
            self.db.close()

    def create_user(self, username: str, role: str, password: str,
                    totp_secret: str | None = None) -> None:
        if not USER_RE.fullmatch(username) or role not in ROLES or len(password) < 12:
            raise AuthError("invalid user, role or password")
        if role == "lead" and not totp_secret:
            raise AuthError("lead requires TOTP")
        if totp_secret:
            try:
                if len(base64.b32decode(totp_secret, casefold=True)) < 20:
                    raise AuthError("TOTP secret too short")
            except (ValueError, base64.binascii.Error) as exc:
                raise AuthError("invalid TOTP secret") from exc
        encoded = self.hasher.hash(password)
        with self.lock:
            self.db.execute(
                "INSERT INTO users(username, role, password_hash, totp_secret) "
                "VALUES(?, ?, ?, ?)", (username, role, encoded, totp_secret))

    def login(self, username: str, password: str, code: str = "",
              now: int | None = None) -> Session:
        at = int(time.time()) if now is None else now
        with self.lock:
            row = self.db.execute("SELECT * FROM users WHERE username=?", (username,)).fetchone()
            encoded = row["password_hash"] if row else self.dummy_hash
            password_ok = self.hasher.verify(encoded, password)
            locked = bool(row and row["locked_until"] > at)
            counter = None
            if row and row["totp_secret"] and password_ok and not locked:
                counter = matching_totp_counter(row["totp_secret"], code, at,
                                                row["last_totp"])
            valid = bool(row and password_ok and not locked and
                         (not row["totp_secret"] or counter is not None))
            if not valid:
                if row and not locked:
                    failures = row["failures"] + 1
                    until = at + LOCK_SECONDS if failures >= MAX_FAILURES else 0
                    self.db.execute(
                        "UPDATE users SET failures=?, locked_until=? WHERE username=?",
                        (failures, until, username))
                raise AuthError("invalid credentials")
            token = secrets.token_urlsafe(32)
            csrf = secrets.token_urlsafe(32)
            self.db.execute(
                "UPDATE users SET failures=0, locked_until=0, last_totp=? WHERE username=?",
                (counter if counter is not None else row["last_totp"], username))
            self.db.execute(
                "INSERT INTO sessions(token_hash, username, csrf, created, last_seen) "
                "VALUES(?, ?, ?, ?, ?)", (self._digest(token), username, csrf, at, at))
            return Session(username, row["role"], csrf, token)

    @staticmethod
    def _digest(token: str) -> str:
        return hashlib.sha256(token.encode("ascii")).hexdigest()

    def session(self, token: str, now: int | None = None) -> Session | None:
        if not token or len(token) > 128 or not token.isascii():
            return None
        at = int(time.time()) if now is None else now
        with self.lock:
            row = self.db.execute(
                "SELECT s.*, u.role FROM sessions s JOIN users u USING(username) "
                "WHERE token_hash=?", (self._digest(token),)).fetchone()
            if not row:
                return None
            if at - row["last_seen"] > IDLE_SECONDS or at - row["created"] > MAX_SECONDS:
                self.db.execute("DELETE FROM sessions WHERE token_hash=?",
                                (self._digest(token),))
                return None
            self.db.execute("UPDATE sessions SET last_seen=? WHERE token_hash=?",
                            (at, self._digest(token)))
            return Session(row["username"], row["role"], row["csrf"])

    def logout(self, token: str) -> None:
        if token and len(token) <= 128 and token.isascii():
            with self.lock:
                self.db.execute("DELETE FROM sessions WHERE token_hash=?",
                                (self._digest(token),))
