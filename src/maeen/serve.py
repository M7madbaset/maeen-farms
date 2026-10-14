"""Run the API (SIMULATION ONLY).
Required env: MAEEN_JWT_SECRET (>=32 chars). Optional: MAEEN_DB_PATH (default maeen.db), MAEEN_SCENARIO (40|142),
MAEEN_DEV_USERS_PASSWORD (creates DEV users owner1, agro1, op1, aud1, fin1, admin1 - never use in production),
MAEEN_ENABLE_DOCS=1, MAEEN_HOST (default 127.0.0.1), MAEEN_PORT (default 8000)."""
from __future__ import annotations
import os, sys
from .api import create_app
from .auth import create_user
from .runtime import build_runtime

DEV_USERS = {"owner1": ["owner"], "agro1": ["agronomist"], "op1": ["irrigation_operator"],
             "aud1": ["auditor"], "fin1": ["finance_officer"], "admin1": ["admin"]}


def build_from_env():
    secret = os.environ.get("MAEEN_JWT_SECRET")
    if not secret:
        sys.exit("MAEEN_JWT_SECRET is required (>=32 random characters). Example: export MAEEN_JWT_SECRET=$(openssl rand -hex 32)")
    rt = build_runtime(os.environ.get("MAEEN_SCENARIO", "40"), "simulation",
                       db_path=os.environ.get("MAEEN_DB_PATH", "maeen.db"))
    pw = os.environ.get("MAEEN_DEV_USERS_PASSWORD")
    if pw:
        print("WARNING: creating DEV users with a shared password - development only.", file=sys.stderr)
        for u, roles in DEV_USERS.items():
            create_user(rt.db, u, pw, roles)
    return create_app(rt, secret, enable_docs=os.environ.get("MAEEN_ENABLE_DOCS") == "1")


def main():
    import uvicorn
    uvicorn.run(build_from_env(), host=os.environ.get("MAEEN_HOST", "127.0.0.1"),
                port=int(os.environ.get("MAEEN_PORT", "8000")))


if __name__ == "__main__":
    main()
