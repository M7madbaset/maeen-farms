import pytest
pytest.importorskip("jwt")
import json, pytest, time
from maeen import auth
from maeen.runtime import build_runtime

SECRET = "s" * 40
PW = "correct-horse-battery"


def test_password_hashing_is_salted_slow_and_never_plaintext():
    assert auth.SCRYPT_N >= 2 ** 10
    h1, h2 = auth.hash_password(PW), auth.hash_password(PW)
    assert h1 != h2 and PW not in h1 and h1.startswith("scrypt$")
    assert auth.verify_password(PW, h1) and not auth.verify_password(PW + "x", h1)
    assert not auth.verify_password(PW, "garbage") and not auth.verify_password(PW, "md5$1$2$3$4$5")


def test_password_and_username_and_role_policy():
    rt = build_runtime("40")
    with pytest.raises(ValueError): auth.hash_password("short")
    with pytest.raises(ValueError): auth.create_user(rt.db, "Bad User!", PW, ["owner"])
    with pytest.raises(ValueError): auth.create_user(rt.db, "okuser", PW, ["superuser"])
    with pytest.raises(ValueError): auth.create_user(rt.db, "okuser", PW, [])


def test_token_roundtrip_expiry_tamper_and_alg_none():
    import jwt
    t = auth.TokenService(SECRET, ttl_seconds=60)
    tok = t.issue("alice", now=1000)
    assert t.verify(tok, now=1010) == "alice"
    with pytest.raises(auth.AuthError): t.verify(tok, now=1061)                       # expired
    with pytest.raises(auth.AuthError): t.verify(tok[:-3] + "AAA", now=1010)          # bad signature
    with pytest.raises(auth.AuthError): auth.TokenService("o" * 40).verify(tok, now=1010)   # other secret
    none_tok = jwt.encode({"sub": "alice", "iat": 1000, "exp": 99999999999}, key=None, algorithm="none")
    with pytest.raises(auth.AuthError): t.verify(none_tok)
    with pytest.raises(auth.AuthError): t.verify("not.a.token")
    other_alg = jwt.encode({"sub": "alice", "iat": 1000, "exp": 99999999999}, SECRET, algorithm="HS512")
    with pytest.raises(auth.AuthError): t.verify(other_alg)                            # algorithm pinned to HS256


def test_weak_secret_rejected():
    with pytest.raises(ValueError): auth.TokenService("too-short")
    with pytest.raises(ValueError): auth.TokenService("")


def test_login_lockout_and_recovery_window():
    rt = build_runtime("40"); auth.create_user(rt.db, "alice", PW, ["owner"])
    now = [0.0]; lim = auth.LoginLimiter(max_failures=3, window=100, clock=lambda: now[0])
    for _ in range(3): assert auth.authenticate(rt.db, lim, "alice", "wrong-password!") is False
    with pytest.raises(auth.AuthError): auth.authenticate(rt.db, lim, "alice", PW)      # even correct pw is locked out
    now[0] = 101
    assert auth.authenticate(rt.db, lim, "alice", PW) is True


def test_unknown_user_and_inactive_user_cannot_authenticate():
    rt = build_runtime("40"); lim = auth.LoginLimiter()
    assert auth.authenticate(rt.db, lim, "ghost", PW) is False
    auth.create_user(rt.db, "bob", PW, ["owner"], active=False)
    assert auth.authenticate(rt.db, lim, "bob", PW) is False


def test_user_directory_is_live_and_policy_uses_it():
    rt = build_runtime("40"); auth.create_user(rt.db, "carol", PW, ["agronomist"])
    rt.policy.users = auth.UserDirectory(rt.db)
    assert rt.policy.validate_approver("carol", "request_valve_command").allowed
    auth.set_roles(rt.db, "carol", ["auditor"])
    assert rt.policy.validate_approver("carol", "request_valve_command").code == "insufficient_role"
    auth.set_roles(rt.db, "carol", ["agronomist"]); auth.set_active(rt.db, "carol", False)
    assert rt.policy.validate_approver("carol", "request_valve_command").code == "unknown_user"
