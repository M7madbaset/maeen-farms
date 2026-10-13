"""Minimal forward-only PostgreSQL migration runner (checksummed, transactional, lock-protected).
Requires the optional dependency:  pip install "psycopg[binary]"."""
from __future__ import annotations
import argparse, hashlib, os, re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
MIGRATIONS_DIR = ROOT / "db" / "migrations"
SEEDS_DIR = ROOT / "db" / "seeds"
_NAME = re.compile(r"^(\d{3})_[a-z0-9_]+\.sql$")
LOCK_ID = 727002


class MigrationError(RuntimeError):
    pass


def migration_files(directory: Path | None = None) -> list[Path]:
    files = sorted((directory or MIGRATIONS_DIR).glob("*.sql"))
    nums = []
    for f in files:
        m = _NAME.match(f.name)
        if not m:
            raise MigrationError(f"bad migration file name: {f.name}")
        nums.append(int(m.group(1)))
    if nums != list(range(1, len(nums) + 1)):
        raise MigrationError(f"migration numbers must be consecutive from 001, got {nums}")
    return files


def checksum(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def migrate(dsn: str, directory: Path | None = None, seed: bool = False) -> list[str]:
    """Apply pending migrations. Returns the names applied in this run."""
    import psycopg
    files = migration_files(directory)
    applied_now: list[str] = []
    with psycopg.connect(dsn, autocommit=True) as conn:
        conn.execute("SELECT pg_advisory_lock(%s)", (LOCK_ID,))
        try:
            conn.execute("""CREATE TABLE IF NOT EXISTS schema_migration (
                version text PRIMARY KEY, checksum char(64) NOT NULL, applied_at timestamptz NOT NULL DEFAULT now())""")
            done = dict(conn.execute("SELECT version, checksum FROM schema_migration").fetchall())
            for f in files:
                cs = checksum(f)
                if f.name in done:
                    if done[f.name] != cs:
                        raise MigrationError(f"{f.name} was modified after being applied (checksum mismatch)")
                    continue
                with conn.transaction():
                    conn.execute(f.read_text(encoding="utf-8"))
                    conn.execute("INSERT INTO schema_migration(version, checksum) VALUES (%s, %s)", (f.name, cs))
                applied_now.append(f.name)
            missing = set(done) - {f.name for f in files}
            if missing:
                raise MigrationError(f"database has migrations unknown to this checkout: {sorted(missing)}")
            if seed:
                conn.execute((SEEDS_DIR / "dev_seed.sql").read_text(encoding="utf-8"))
        finally:
            conn.execute("SELECT pg_advisory_unlock(%s)", (LOCK_ID,))
    return applied_now


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(description="MAeen PostgreSQL migrations")
    ap.add_argument("--dsn", default=os.environ.get("MAEEN_DATABASE_URL"), help="libpq DSN or URL")
    ap.add_argument("--seed", action="store_true", help="also load DEV-ONLY seed data")
    a = ap.parse_args(argv)
    if not a.dsn:
        ap.error("--dsn or MAEEN_DATABASE_URL required")
    done = migrate(a.dsn, seed=a.seed)
    print("applied:", ", ".join(done) if done else "nothing (up to date)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
