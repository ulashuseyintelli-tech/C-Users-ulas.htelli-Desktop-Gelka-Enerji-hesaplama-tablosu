"""
Tests for PtfDriftLog model + alembic 012.

Feature: ptf-sot-unification, Phase 1 T1.3

Scope (locked):
- Column shape + nullability
- CHECK constraints (severity allowlist, request_hash length)
- Indexes declared
- Basic CRUD (insert + filter by severity='high')
- Alembic 012 chain: down_revision = 011, upgrade/downgrade round-trip

Out of scope for T1.3 (lands in T2.2):
- compute_drift / record_drift helpers
- fail-open behavior when DB insert raises
"""

from __future__ import annotations

import hashlib

import pytest
from sqlalchemy import create_engine, inspect, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import Base

# Importing the module registers PtfDriftLog with Base.metadata so the
# in-memory schema created via create_all() includes the new table. This
# mirrors the pattern used by test_pricing_core.py (see `import app.pricing.schemas`).
from app.ptf_drift_log import PtfDriftLog  # noqa: F401  (metadata side effect)


# ── Hash helper: produce a valid 64-char sha256 hex for fixture rows. ─────────

def _fake_request_hash(seed: str) -> str:
    return hashlib.sha256(seed.encode("utf-8")).hexdigest()


# ── Fixtures ──────────────────────────────────────────────────────────────────

@pytest.fixture()
def db_session():
    """In-memory SQLite session with every registered model materialized."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    # Ensure SQLite enforces CHECK constraints (default ON in modern SQLite,
    # but pragma is cheap and documents the expectation).
    with engine.connect() as conn:
        conn.execute(text("PRAGMA foreign_keys = ON"))

    Base.metadata.create_all(bind=engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    try:
        yield session, engine
    finally:
        session.close()
        engine.dispose()


# ── Schema shape ──────────────────────────────────────────────────────────────

class TestPtfDriftLogSchema:
    """Phase 1 T1.3 — table exists and columns match design §3.1."""

    def test_table_materializes(self, db_session):
        _, engine = db_session
        inspector = inspect(engine)
        assert "ptf_drift_log" in inspector.get_table_names()

    def test_has_expected_columns(self, db_session):
        _, engine = db_session
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("ptf_drift_log")}
        expected = {
            "id",
            "created_at",
            "period",
            "canonical_price",
            "legacy_price",
            "delta_abs",
            "delta_pct",
            "severity",
            "request_hash",
            "customer_id",
        }
        assert set(columns.keys()) == expected

    def test_required_columns_not_null(self, db_session):
        _, engine = db_session
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("ptf_drift_log")}
        # NOT NULL set — design §3.1
        assert columns["period"]["nullable"] is False
        assert columns["canonical_price"]["nullable"] is False
        assert columns["severity"]["nullable"] is False
        assert columns["request_hash"]["nullable"] is False

    def test_optional_columns_nullable(self, db_session):
        _, engine = db_session
        inspector = inspect(engine)
        columns = {col["name"]: col for col in inspector.get_columns("ptf_drift_log")}
        # NULLABLE — legacy read may fail, customer scope may be absent
        assert columns["legacy_price"]["nullable"] is True
        assert columns["delta_abs"]["nullable"] is True
        assert columns["delta_pct"]["nullable"] is True
        assert columns["customer_id"]["nullable"] is True

    def test_indexes_declared(self, db_session):
        _, engine = db_session
        inspector = inspect(engine)
        index_names = {idx["name"] for idx in inspector.get_indexes("ptf_drift_log")}
        assert "ix_ptf_drift_log_created_at" in index_names
        assert "ix_ptf_drift_log_period" in index_names
        assert "ix_ptf_drift_log_request_hash" in index_names


# ── CRUD + filter ─────────────────────────────────────────────────────────────

class TestPtfDriftLogCRUD:
    """Minimum viable persistence: insert, filter by severity='high'."""

    def test_insert_then_select_high_severity(self, db_session):
        session, _ = db_session
        low = PtfDriftLog(
            period="2026-03",
            canonical_price=2500.0,
            legacy_price=2498.0,
            delta_abs=2.0,
            delta_pct=0.08,
            severity="low",
            request_hash=_fake_request_hash("low-001"),
            customer_id=None,
        )
        high = PtfDriftLog(
            period="2026-03",
            canonical_price=2500.0,
            legacy_price=2600.0,
            delta_abs=100.0,
            delta_pct=4.0,
            severity="high",
            request_hash=_fake_request_hash("high-001"),
            customer_id=42,
        )
        session.add_all([low, high])
        session.commit()

        # Tripwire: primary key assigned + created_at populated by DB default.
        assert low.id is not None
        assert high.created_at is not None

        rows = (
            session.query(PtfDriftLog)
            .filter(PtfDriftLog.severity == "high")
            .all()
        )
        assert len(rows) == 1
        assert rows[0].request_hash == _fake_request_hash("high-001")
        assert rows[0].customer_id == 42
        assert rows[0].delta_pct == pytest.approx(4.0)

    def test_insert_with_null_legacy_price(self, db_session):
        """Legacy read failure path — writer in T2.2 still logs severity=low."""
        session, _ = db_session
        row = PtfDriftLog(
            period="2026-03",
            canonical_price=2500.0,
            legacy_price=None,
            delta_abs=None,
            delta_pct=None,
            severity="low",
            request_hash=_fake_request_hash("legacy-miss"),
        )
        session.add(row)
        session.commit()
        assert row.id is not None
        fetched = session.query(PtfDriftLog).filter_by(id=row.id).one()
        assert fetched.legacy_price is None
        assert fetched.delta_abs is None
        assert fetched.delta_pct is None


# ── CHECK constraints ────────────────────────────────────────────────────────

class TestPtfDriftLogConstraints:
    """CHECK constraints guard against pipeline bugs writing junk values."""

    def test_severity_must_be_low_or_high(self, db_session):
        session, _ = db_session
        bad = PtfDriftLog(
            period="2026-03",
            canonical_price=2500.0,
            severity="critical",  # not in allowlist
            request_hash=_fake_request_hash("bad-severity"),
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()

    def test_request_hash_must_be_64_chars(self, db_session):
        session, _ = db_session
        bad = PtfDriftLog(
            period="2026-03",
            canonical_price=2500.0,
            severity="low",
            request_hash="deadbeef",  # 8 chars — too short
        )
        session.add(bad)
        with pytest.raises(IntegrityError):
            session.commit()
        session.rollback()


# ── Alembic migration metadata ───────────────────────────────────────────────

class TestAlembicMigration012:
    """Static checks on 012_add_ptf_drift_log_table.

    We avoid importing the migration module because it imports `alembic.op`,
    and pytest's cwd (backend/) contains a local `alembic/` package which
    shadows the installed alembic library. Parsing the file statically
    (ast + text) is reliable and sufficient to guarantee the revision chain
    and the presence of upgrade/downgrade. The migration is also exercised
    in the subprocess test below against a temporary SQLite file, which
    proves the DDL actually runs under the real alembic CLI.
    """

    MIGRATION_PATH = None  # filled by _migration_path()

    @classmethod
    def _migration_path(cls):
        from pathlib import Path

        p = (
            Path(__file__).resolve().parents[1]
            / "alembic"
            / "versions"
            / "012_add_ptf_drift_log_table.py"
        )
        assert p.exists(), f"migration file missing: {p}"
        return p

    def test_revision_chain_via_ast(self):
        import ast

        source = self._migration_path().read_text(encoding="utf-8")
        tree = ast.parse(source)
        assignments = {}
        for node in tree.body:
            if isinstance(node, ast.Assign):
                for target in node.targets:
                    if isinstance(target, ast.Name):
                        # Only capture string/None literals we care about.
                        if isinstance(node.value, ast.Constant):
                            assignments[target.id] = node.value.value
        assert assignments.get("revision") == "012_add_ptf_drift_log_table"
        assert assignments.get("down_revision") == "011_market_prices_ptf_admin"
        assert assignments.get("branch_labels") is None

    def test_migration_defines_upgrade_and_downgrade(self):
        import ast

        source = self._migration_path().read_text(encoding="utf-8")
        tree = ast.parse(source)
        func_names = {
            node.name for node in tree.body if isinstance(node, ast.FunctionDef)
        }
        assert "upgrade" in func_names
        assert "downgrade" in func_names


class TestAlembicMigration012Roundtrip:
    """Subprocess test: isolate the 011 → 012 transition in a temporary SQLite
    DB and verify upgrade/downgrade/re-upgrade works cleanly. Skipped if the
    alembic CLI is not on PATH.

    Why we stamp instead of running from zero:
    the existing 001..011 migration chain has unrelated issues on a fresh
    SQLite (e.g. migration 004 touches `incidents` before it exists, which
    only worked historically because production evolved iteratively). Those
    issues are orthogonal to T1.3. We therefore `alembic stamp` the chain up
    to 011 (writes alembic_version only, does NOT run earlier DDL) and then
    run a true upgrade/downgrade/re-upgrade cycle just for 012. This proves
    that the only revision under test — ours — is correct in isolation.
    """

    def test_upgrade_then_downgrade_roundtrip(self, tmp_path):
        import shutil
        import subprocess
        from pathlib import Path

        alembic_cmd = shutil.which("alembic")
        if alembic_cmd is None:
            pytest.skip("alembic CLI not available on PATH")

        backend_dir = Path(__file__).resolve().parents[1]
        db_file = tmp_path / "roundtrip.db"
        # NOTE: do NOT set PYTHONPATH=backend here. backend/alembic is a local
        # package (it has __init__.py), so adding backend_dir to sys.path
        # would shadow the installed `alembic` library and the CLI would fail
        # with "No module named 'alembic.config'". alembic.ini's
        # `prepend_sys_path = .` takes care of `app.*` resolution *after* the
        # real alembic package has already loaded.
        env = {
            **__import__("os").environ,
            "DATABASE_URL": f"sqlite:///{db_file.as_posix()}",
        }
        env.pop("PYTHONPATH", None)

        def _run(*args):
            result = subprocess.run(
                [alembic_cmd, *args],
                cwd=str(backend_dir),
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
            return result

        # Stamp the chain up to 011 without executing earlier migrations.
        # This creates the alembic_version table and records 011 as applied,
        # sidestepping pre-existing bugs in migrations 001..010 that are not
        # in T1.3's scope.
        stamp = _run("stamp", "011_market_prices_ptf_admin")
        assert stamp.returncode == 0, (
            f"alembic stamp failed:\nstdout={stamp.stdout}\nstderr={stamp.stderr}"
        )

        # Now run the migration we actually care about: 011 → 012.
        up = _run("upgrade", "head")
        assert up.returncode == 0, (
            f"alembic upgrade head failed:\nstdout={up.stdout}\nstderr={up.stderr}"
        )

        import sqlite3

        with sqlite3.connect(db_file) as conn:
            tables = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            version = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
            indexes = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master "
                    "WHERE type='index' AND tbl_name='ptf_drift_log'"
                ).fetchall()
            }
        assert "ptf_drift_log" in tables
        assert version == "012_add_ptf_drift_log_table"
        # All three secondary indexes must be present.
        assert "ix_ptf_drift_log_created_at" in indexes
        assert "ix_ptf_drift_log_period" in indexes
        assert "ix_ptf_drift_log_request_hash" in indexes

        # Downgrade: drop the table + return to 011.
        down = _run("downgrade", "-1")
        assert down.returncode == 0, (
            f"alembic downgrade -1 failed:\nstdout={down.stdout}\nstderr={down.stderr}"
        )
        with sqlite3.connect(db_file) as conn:
            tables_after_down = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
            version_after_down = conn.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchone()[0]
        assert "ptf_drift_log" not in tables_after_down
        assert version_after_down == "011_market_prices_ptf_admin"

        # Re-upgrade — idempotency across the full cycle.
        up2 = _run("upgrade", "head")
        assert up2.returncode == 0, (
            f"alembic re-upgrade failed:\nstdout={up2.stdout}\nstderr={up2.stderr}"
        )
        with sqlite3.connect(db_file) as conn:
            tables_final = {
                r[0]
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                ).fetchall()
            }
        assert "ptf_drift_log" in tables_final


# ── Fail-open write helper tests ─────────────────────────────────────────────

from app.ptf_drift_log import DriftRecord, write_drift_record


class TestWriteDriftRecordFailOpen:
    """Critical invariant: DB insert failure MUST NOT propagate to caller.

    If this test fails, a drift-logging outage becomes a pricing outage.
    That is the single most dangerous failure mode in the PTF migration.
    """

    def test_successful_write_returns_true(self, db_session):
        """Happy path: valid record → committed → True."""
        session, _ = db_session
        rec = DriftRecord(
            period="2026-03",
            canonical_price=2500.0,
            legacy_price=2498.0,
            delta_abs=2.0,
            delta_pct=0.08,
            severity="low",
            request_hash=_fake_request_hash("happy-path"),
        )
        result = write_drift_record(session, rec)
        assert result is True
        # Verify row landed
        from app.ptf_drift_log import PtfDriftLog

        count = session.query(PtfDriftLog).count()
        assert count == 1

    def test_db_error_returns_false_not_raises(self, db_session):
        """DB insert fails → returns False, does NOT raise into pricing path."""
        from unittest.mock import patch as mock_patch

        session, _ = db_session
        rec = DriftRecord(
            period="2026-03",
            canonical_price=2500.0,
            severity="low",
            request_hash=_fake_request_hash("db-dead"),
        )
        # Simulate a DB failure by making commit raise OperationalError
        with mock_patch.object(
            session, "commit", side_effect=Exception("simulated DB failure")
        ):
            # This MUST NOT raise
            result = write_drift_record(session, rec)
        assert result is False

    def test_check_constraint_violation_returns_false(self, db_session):
        """Invalid severity → CHECK fails → returns False, no raise."""
        session, _ = db_session
        rec = DriftRecord(
            period="2026-03",
            canonical_price=2500.0,
            severity="critical",  # violates CHECK
            request_hash=_fake_request_hash("bad-severity"),
        )
        result = write_drift_record(session, rec)
        assert result is False

    def test_none_record_returns_false(self, db_session):
        """Defensive: None record → False, no raise."""
        session, _ = db_session
        result = write_drift_record(session, None)
        assert result is False

    def test_short_hash_returns_false(self, db_session):
        """request_hash too short → CHECK fails → False."""
        session, _ = db_session
        rec = DriftRecord(
            period="2026-03",
            canonical_price=2500.0,
            severity="low",
            request_hash="tooshort",  # 8 chars, needs 64
        )
        result = write_drift_record(session, rec)
        assert result is False

    def test_successful_write_with_null_legacy(self, db_session):
        """Legacy read failure path: legacy_price=None still writes."""
        session, _ = db_session
        rec = DriftRecord(
            period="2026-01",
            canonical_price=3000.0,
            legacy_price=None,
            delta_abs=None,
            delta_pct=None,
            severity="low",
            request_hash=_fake_request_hash("legacy-miss-write"),
            customer_id=7,
        )
        result = write_drift_record(session, rec)
        assert result is True
