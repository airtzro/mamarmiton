"""Passwords, session tokens, invite codes and the login rate limiter."""

from __future__ import annotations

import hashlib
import re
import secrets
import threading
import time
from collections import defaultdict, deque

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerificationError, VerifyMismatchError

_hasher = PasswordHasher()  # argon2id with the library's current recommended parameters
# verified against when the username does not exist, so both cases take the same time
_DUMMY_HASH = _hasher.hash(secrets.token_urlsafe(16))

USERNAME_RE = re.compile(r"[a-z0-9][a-z0-9._-]{1,31}")
MIN_PASSWORD = 8
MAX_PASSWORD = 256  # argon2 is slow on purpose; do not let anyone send megabytes


def normalize_username(name: str) -> str:
    return name.strip().lower()


def valid_username(name: str) -> bool:
    return bool(USERNAME_RE.fullmatch(name))


def password_problem(password: str, username: str = "") -> str | None:
    """A French message explaining why the password is refused, or None."""
    if len(password) < MIN_PASSWORD:
        return f"Le mot de passe doit faire au moins {MIN_PASSWORD} caractères."
    if len(password) > MAX_PASSWORD:
        return "Le mot de passe est trop long."
    if username and password.strip().lower() == username.lower():
        return "Le mot de passe ne peut pas être le nom d'utilisateur."
    if len(set(password)) < 4:
        return "Le mot de passe est trop simple."
    return None


def hash_password(password: str) -> str:
    return _hasher.hash(password)


def verify_password(stored_hash: str | None, password: str) -> bool:
    if len(password) > MAX_PASSWORD:
        return False
    try:
        return _hasher.verify(stored_hash or _DUMMY_HASH, password) and stored_hash is not None
    except (VerifyMismatchError, VerificationError, InvalidHashError):
        return False


def needs_rehash(stored_hash: str) -> bool:
    return _hasher.check_needs_rehash(stored_hash)


def new_token() -> str:
    return secrets.token_urlsafe(32)


def new_invite_code() -> str:
    # easy to read aloud or type: 4 groups of 4 letters/digits, no look-alikes
    alphabet = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"
    raw = "".join(secrets.choice(alphabet) for _ in range(16))
    return "-".join(raw[i:i + 4] for i in range(0, 16, 4))


def normalize_invite(code: str) -> str:
    raw = re.sub(r"[^A-Za-z0-9]", "", code).upper()
    return "-".join(raw[i:i + 4] for i in range(0, len(raw), 4))


def sha256(value: str) -> str:
    return hashlib.sha256(value.encode()).hexdigest()


class LoginLimiter:
    """Counts failed logins per (IP, username) and per IP in a sliding window.

    In memory: a restart clears it, which is fine for a family server. Run a
    single worker process so all requests share one limiter.
    """

    def __init__(self, attempts: int, window_s: int):
        self.attempts = attempts
        self.window_s = window_s
        self._fails: dict[tuple[str, str], deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _recent(self, key: tuple[str, str], now: float) -> deque[float]:
        q = self._fails.get(key)
        if q is None:
            return deque()
        while q and now - q[0] > self.window_s:
            q.popleft()
        if not q:
            del self._fails[key]  # do not keep one entry per username an attacker ever tried
        return q

    def retry_after(self, ip: str, username: str) -> int:
        """Seconds to wait before another try, 0 if allowed."""
        now = time.monotonic()
        with self._lock:
            waits = []
            # a stricter per-account limit, and a looser per-IP one (guessing many names)
            for key, limit in (((ip, username), self.attempts), ((ip, ""), self.attempts * 4)):
                q = self._recent(key, now)
                if len(q) >= limit:
                    waits.append(int(self.window_s - (now - q[0])) + 1)
            return max(waits, default=0)

    def failed(self, ip: str, username: str) -> None:
        now = time.monotonic()
        with self._lock:
            if len(self._fails) > 5000:
                for key in list(self._fails):
                    self._recent(key, now)
            self._fails[(ip, username)].append(now)
            self._fails[(ip, "")].append(now)

    def succeeded(self, ip: str, username: str) -> None:
        with self._lock:
            self._fails.pop((ip, username), None)
