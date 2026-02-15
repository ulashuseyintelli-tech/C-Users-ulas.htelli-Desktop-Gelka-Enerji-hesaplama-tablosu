"""
PR-9: SQLite-backed StateStore implementation.

Transactional, uses UNIQUE constraints for CAS semantics.
Suitable for realistic single-node testing with actual persistence guarantees.
"""
from __future__ import annotations

import json
import sqlite3
import threading
from typing import Optional

from .policy_engine import AuditEntry, Override, OverrideType, OverrideScope
from .state_store import StateStore, DedupResult


class SqliteStateStore(StateStore):
    """
    SQLite-backed shared state store.
    Uses UNIQUE constraints + INSERT OR IGNORE for CAS.
    Thread-safe via sqlite3's internal locking + our serialization.
    """

    def __init__(self, db_path: str = ":memory:") -> None:
        self._lock = threading.Lock()
        self._conn = sqlite3.connect(db_path, check_same_thread=False)
        self._conn.execute("PRAGMA journal_mode=WAL")
        self._create_tables()

    def _create_tables(self) -> None:
        with self._conn:
            self._conn.executescript("""
                CREATE TABLE IF NOT EXISTS events (
                    event_id TEXT PRIMARY KEY,
                    instance_id TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS audit_log (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp_ms INTEGER NOT NULL,
                    action TEXT NOT NULL,
                    detail TEXT NOT NULL DEFAULT '',
                    override_key TEXT,
                    payload TEXT NOT NULL DEFAULT '{}'
                );
                CREATE UNIQUE INDEX IF NOT EXISTS idx_audit_override_key
                    ON audit_log(override_key) WHERE override_key IS NOT NULL;
                CREATE TABLE IF NOT EXISTS overrides (
                    idempotency_key TEXT PRIMARY KEY,
                    override_type TEXT NOT NULL,
                    scope TEXT NOT NULL,
                    scope_value TEXT,
                    ttl_seconds INTEGER NOT NULL,
                    created_at_ms INTEGER NOT NULL,
                    reason TEXT NOT NULL DEFAULT '',
                    created_by TEXT NOT NULL DEFAULT ''
                );
            """)

    # -- Dedup --

    def try_claim_event(self, event_id: str, instance_id: str) -> DedupResult:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO events (event_id, instance_id) VALUES (?, ?)",
                    (event_id, instance_id),
                )
                self._conn.commit()
                return DedupResult(event_id=event_id, is_new=True, owner_instance=instance_id)
            except sqlite3.IntegrityError:
                row = self._conn.execute(
                    "SELECT instance_id FROM events WHERE event_id = ?", (event_id,)
                ).fetchone()
                owner = row[0] if row else "unknown"
                return DedupResult(event_id=event_id, is_new=False, owner_instance=owner)

    def is_claimed(self, event_id: str) -> bool:
        with self._lock:
            row = self._conn.execute(
                "SELECT 1 FROM events WHERE event_id = ?", (event_id,)
            ).fetchone()
            return row is not None

    # -- Audit --

    def append_audit(self, entry: AuditEntry) -> bool:
        with self._lock:
            override_key = None
            if entry.override and entry.override.idempotency_key:
                override_key = entry.override.idempotency_key
            payload = json.dumps({
                "override": repr(entry.override) if entry.override else None,
                "policy_input": repr(entry.policy_input) if entry.policy_input else None,
                "decision": repr(entry.decision) if entry.decision else None,
            })
            try:
                self._conn.execute(
                    "INSERT INTO audit_log (timestamp_ms, action, detail, override_key, payload) "
                    "VALUES (?, ?, ?, ?, ?)",
                    (entry.timestamp_ms, entry.action, entry.detail, override_key, payload),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def query_audit(self, last_n: int = 50) -> list[AuditEntry]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT timestamp_ms, action, detail FROM audit_log "
                "ORDER BY id DESC LIMIT ?", (last_n,)
            ).fetchall()
            return [
                AuditEntry(
                    timestamp_ms=r[0], action=r[1], detail=r[2],
                    override=None, policy_input=None, decision=None,
                )
                for r in rows
            ]

    def audit_count(self) -> int:
        with self._lock:
            row = self._conn.execute("SELECT COUNT(*) FROM audit_log").fetchone()
            return row[0] if row else 0

    # -- Overrides --

    def put_override(self, override: Override) -> bool:
        with self._lock:
            try:
                self._conn.execute(
                    "INSERT INTO overrides "
                    "(idempotency_key, override_type, scope, scope_value, "
                    " ttl_seconds, created_at_ms, reason, created_by) "
                    "VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        override.idempotency_key,
                        override.override_type.value,
                        override.scope.value,
                        override.scope_value,
                        override.ttl_seconds,
                        override.created_at_ms,
                        override.reason,
                        override.created_by,
                    ),
                )
                self._conn.commit()
                return True
            except sqlite3.IntegrityError:
                return False

    def get_active_overrides(self, now_ms: int) -> list[Override]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT idempotency_key, override_type, scope, scope_value, "
                "       ttl_seconds, created_at_ms, reason, created_by "
                "FROM overrides "
                "WHERE created_at_ms + (ttl_seconds * 1000) > ?",
                (now_ms,),
            ).fetchall()
            return [self._row_to_override(r) for r in rows]

    def expire_overrides(self, now_ms: int) -> int:
        with self._lock:
            cursor = self._conn.execute(
                "DELETE FROM overrides WHERE created_at_ms + (ttl_seconds * 1000) <= ?",
                (now_ms,),
            )
            self._conn.commit()
            return cursor.rowcount

    @staticmethod
    def _row_to_override(row: tuple) -> Override:
        return Override(
            idempotency_key=row[0],
            override_type=OverrideType(row[1]),
            scope=OverrideScope(row[2]),
            scope_value=row[3],
            ttl_seconds=row[4],
            created_at_ms=row[5],
            reason=row[6],
            created_by=row[7],
        )
