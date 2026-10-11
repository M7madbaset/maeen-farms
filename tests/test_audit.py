import pytest, sqlite3
from maeen.runtime import build_runtime


def test_chain_verifies_and_triggers_block_edits():
    rt = build_runtime("40")
    rt.audit.append("a", "x", "c1", {"k": 1})
    assert rt.audit.verify() == (True, None)
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("UPDATE audit_event SET actor='evil' WHERE id=1")
    with pytest.raises(sqlite3.DatabaseError):
        rt.db.execute("DELETE FROM audit_event WHERE id=1")


def test_privileged_tampering_is_detected():
    rt = build_runtime("40")
    rt.audit.append("a", "x", "c1", {"k": 1})
    rt.audit.append("a", "y", "c1", {"k": 2})
    rt.db.execute("DROP TRIGGER audit_no_update")          # attacker with DB privileges
    rt.db.execute("UPDATE audit_event SET payload='{\"k\":999}' WHERE id=2")
    ok, bad = rt.audit.verify()
    assert ok is False and bad == 2


def test_arabic_text_round_trips_in_audit():
    rt = build_runtime("40")
    rt.audit.append("a", "note", "c", {"msg": "نقص في المياه ٤٥"})
    assert rt.audit.trail("c")[0]["payload"] and "نقص في المياه ٤٥" in rt.audit.trail("c")[0]["payload"]
