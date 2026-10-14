from __future__ import annotations
import sqlite3

SCHEMA = """
CREATE TABLE IF NOT EXISTS audit_event (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT NOT NULL, actor TEXT NOT NULL,
  event_type TEXT NOT NULL, correlation_id TEXT, payload TEXT NOT NULL,
  prev_hash TEXT NOT NULL, hash TEXT NOT NULL);
CREATE TRIGGER IF NOT EXISTS audit_no_update BEFORE UPDATE ON audit_event
  BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_no_delete BEFORE DELETE ON audit_event
  BEGIN SELECT RAISE(ABORT, 'audit_event is append-only'); END;
CREATE TABLE IF NOT EXISTS workflow (
  id TEXT PRIMARY KEY, type TEXT NOT NULL, event_id TEXT UNIQUE NOT NULL,
  state TEXT NOT NULL, data TEXT NOT NULL, created_at TEXT, updated_at TEXT);
CREATE TABLE IF NOT EXISTS approval (
  approval_id TEXT PRIMARY KEY, workflow_id TEXT, approver_user_id TEXT,
  fingerprints TEXT, created_at TEXT, expires_at TEXT);
CREATE TABLE IF NOT EXISTS device_command (
  idempotency_key TEXT PRIMARY KEY, command_id TEXT, workflow_id TEXT,
  device_id TEXT, status TEXT, result TEXT, ts TEXT);
CREATE TABLE IF NOT EXISTS notification (
  id INTEGER PRIMARY KEY AUTOINCREMENT, ts TEXT, workflow_id TEXT, audience TEXT,
  severity TEXT, text_en TEXT, text_ar TEXT, simulated INTEGER);
CREATE TABLE IF NOT EXISTS anomaly_case (
  workflow_id TEXT PRIMARY KEY, predicted TEXT, confidence REAL, confirmed TEXT,
  confirmed_by TEXT, note TEXT, ts TEXT, confirmed_ts TEXT);
CREATE TABLE IF NOT EXISTS api_user (
  username TEXT PRIMARY KEY, password_hash TEXT NOT NULL, roles TEXT NOT NULL,
  active INTEGER NOT NULL DEFAULT 1, created_at TEXT);
CREATE TABLE IF NOT EXISTS agent_execution (
  id TEXT PRIMARY KEY, agent_id TEXT, agent_version TEXT, correlation_id TEXT,
  status TEXT, output TEXT, ts TEXT);
"""


def connect(path: str = ":memory:") -> sqlite3.Connection:
    conn = sqlite3.connect(path, check_same_thread=False)   # API serialises access with a lock
    conn.row_factory = sqlite3.Row
    conn.executescript(SCHEMA)
    return conn
