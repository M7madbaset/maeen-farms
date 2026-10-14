"""Authentication: scrypt password hashes, HS256 tokens (PyJWT), login lockout, live role lookup.
Roles are read from the database on EVERY request, never trusted from the token, so revoking
a role or deactivating a user takes effect immediately."""
from __future__ import annotations
import base64, hashlib, hmac, json, os, re, secrets, time
import jwt

ALGORITHM = "HS256"
MIN_SECRET_LEN = 32
MIN_PASSWORD_LEN = 12
SCRYPT_N = 2 ** 14          # production cost; tests may lower it for speed (stored in each hash)
_R, _P = 8, 1
VALID_ROLES = {"owner", "agronomist", "irrigation_operator", "field_supervisor", "maintenance_technician",
               "procurement_officer", "finance_officer", "auditor", "admin"}
_USERNAME = re.compile(r"^[a-z][a-z0-9_.-]{2,31}$")


class AuthError(Exception):
    pass


def hash_password(password: str) -> str:
    if len(password) < MIN_PASSWORD_LEN:
        raise ValueError(f"password must be at least {MIN_PASSWORD_LEN} characters")
    salt = os.urandom(16)
    h = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=SCRYPT_N, r=_R, p=_P, dklen=32)
    b = lambda x: base64.b64encode(x).decode()
    return f"scrypt${SCRYPT_N}${_R}${_P}${b(salt)}${b(h)}"


def verify_password(password: str, stored: str) -> bool:
    try:
        algo, n, r, p, salt, h = stored.split("$")
        if algo != "scrypt":
            return False
        calc = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r),
                              p=int(p), dklen=32)
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


# Computed once: lets login spend comparable time for unknown users (no username enumeration by timing).
_DUMMY = hash_password("not-a-real-password-for-timing")


def create_user(db, username: str, password: str, roles, active: bool = True):
    from datetime import datetime, timezone
    if not _USERNAME.match(username):
        raise ValueError("username must be 3-32 chars: lowercase letters, digits, . _ -")
    roles = set(roles)
    if not roles or not roles <= VALID_ROLES:
        raise ValueError(f"roles must be a non-empty subset of {sorted(VALID_ROLES)}")
    db.execute("INSERT OR REPLACE INTO api_user VALUES (?,?,?,?,?)",
               (username, hash_password(password), json.dumps(sorted(roles)), int(active),
                datetime.now(timezone.utc).isoformat()))
    db.commit()


def set_roles(db, username: str, roles):
    db.execute("UPDATE api_user SET roles=? WHERE username=?", (json.dumps(sorted(set(roles))), username))
    db.commit()


def set_active(db, username: str, active: bool):
    db.execute("UPDATE api_user SET active=? WHERE username=?", (int(active), username))
    db.commit()


class UserDirectory:
    """Dict-like view used by the policy engine: .get(user_id) -> set(roles) | None. Always live."""
    def __init__(self, db):
        self.db = db

    def get(self, username, default=None):
        row = self.db.execute("SELECT roles, active FROM api_user WHERE username=?", (username,)).fetchone()
        if row is None or not row["active"]:
            return default
        return set(json.loads(row["roles"]))


class TokenService:
    def __init__(self, secret: str, ttl_seconds: int = 900):
        if not secret or len(secret) < MIN_SECRET_LEN:
            raise ValueError(f"JWT secret must be at least {MIN_SECRET_LEN} characters")
        self._secret, self.ttl = secret, ttl_seconds

    def issue(self, username: str, now: float | None = None) -> str:
        now = int(now if now is not None else time.time())
        return jwt.encode({"sub": username, "iat": now, "exp": now + self.ttl, "jti": secrets.token_hex(8)},
                          self._secret, algorithm=ALGORITHM)

    def verify(self, token: str, now: float | None = None) -> str:
        try:
            claims = jwt.decode(token, self._secret, algorithms=[ALGORITHM],      # pinned: rejects alg=none/RS confusion
                                options={"require": ["sub", "exp", "iat"], "verify_exp": False})
            if int(claims["exp"]) < int(now if now is not None else time.time()):
                raise AuthError("token expired")
            return str(claims["sub"])
        except AuthError:
            raise
        except Exception as e:
            raise AuthError("invalid token") from e


class LoginLimiter:
    """Per-username lockout: `max_failures` failures inside `window` seconds => locked until window passes."""
    def __init__(self, max_failures: int = 5, window: float = 300.0, clock=time.monotonic):
        self.max, self.window, self.clock, self._f = max_failures, window, clock, {}

    def locked(self, username: str) -> bool:
        now = self.clock()
        hits = [t for t in self._f.get(username, []) if now - t < self.window]
        self._f[username] = hits
        return len(hits) >= self.max

    def failed(self, username: str):
        self._f.setdefault(username, []).append(self.clock())

    def succeeded(self, username: str):
        self._f.pop(username, None)


def authenticate(db, limiter: LoginLimiter, username: str, password: str) -> bool:
    """True only for an active user with the right password. Constant-ish work for unknown users."""
    if limiter.locked(username):
        raise AuthError("locked")
    row = db.execute("SELECT password_hash, active FROM api_user WHERE username=?", (username,)).fetchone()
    stored = row["password_hash"] if row else _DUMMY
    ok = verify_password(password, stored) and bool(row) and bool(row["active"])
    (limiter.succeeded if ok else limiter.failed)(username)
    return ok
