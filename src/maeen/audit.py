"""Append-only, hash-chained audit log.
DB triggers block UPDATE/DELETE; the hash chain detects tampering by a privileged
user who drops the triggers. NOTE: to be truly tamper-evident the chain head must
be anchored outside this database (not implemented in slice 1)."""
from __future__ import annotations
import hashlib, json

GENESIS = "0" * 64


def _canon(obj) -> str:
    return json.dumps(obj, sort_keys=True, ensure_ascii=False, separators=(",", ":"), default=str)


class AuditLog:
    def __init__(self, conn, clock):
        self.conn, self.clock = conn, clock

    def _last_hash(self) -> str:
        row = self.conn.execute("SELECT hash FROM audit_event ORDER BY id DESC LIMIT 1").fetchone()
        return row["hash"] if row else GENESIS

    def append(self, actor: str, event_type: str, correlation_id: str | None, payload: dict) -> int:
        ts = self.clock.now().isoformat()
        prev = self._last_hash()
        body = _canon({"ts": ts, "actor": actor, "event_type": event_type,
                       "correlation_id": correlation_id, "payload": payload})
        h = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
        cur = self.conn.execute(
            "INSERT INTO audit_event(ts,actor,event_type,correlation_id,payload,prev_hash,hash)"
            " VALUES (?,?,?,?,?,?,?)",
            (ts, actor, event_type, correlation_id, _canon(payload), prev, h))
        self.conn.commit()
        return cur.lastrowid

    def verify(self) -> tuple[bool, int | None]:
        prev = GENESIS
        for r in self.conn.execute("SELECT * FROM audit_event ORDER BY id"):
            body = _canon({"ts": r["ts"], "actor": r["actor"], "event_type": r["event_type"],
                           "correlation_id": r["correlation_id"], "payload": json.loads(r["payload"])})
            expect = hashlib.sha256((prev + body).encode("utf-8")).hexdigest()
            if r["prev_hash"] != prev or r["hash"] != expect:
                return False, r["id"]
            prev = r["hash"]
        return True, None

    def trail(self, correlation_id: str | None = None) -> list[dict]:
        q, args = "SELECT * FROM audit_event", ()
        if correlation_id:
            q, args = q + " WHERE correlation_id=?", (correlation_id,)
        return [dict(r) for r in self.conn.execute(q + " ORDER BY id", args)]
