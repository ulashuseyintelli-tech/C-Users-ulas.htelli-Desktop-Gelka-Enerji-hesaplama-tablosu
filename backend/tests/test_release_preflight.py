"""
PR-15: Release Preflight CLI — smoke tests.

Validates preflight dry-run behavior, exit codes, JSON output format,
spec_hash presence, and artifact file creation.
"""
import json
import pytest
from pathlib import Path

from backend.app.testing.release_preflight import (
    run_preflight,
    _EXIT_OK,
    _EXIT_HOLD,
    _EXIT_BLOCK,
    _EXIT_USAGE,
)


class TestPreflightDryRun:
    """Req 6.2: dry-run mode produces BLOCK verdict."""

    def test_dry_run_returns_block(self):
        """No signal data → BLOCK (NO_TIER_DATA + NO_FLAKE_DATA)."""
        exit_code = run_preflight(json_mode=False, output_dir=None)
        assert exit_code == _EXIT_BLOCK

    def test_dry_run_exit_code_is_2(self):
        """Exit code contract: BLOCK = 2."""
        exit_code = run_preflight()
        assert exit_code == 2


class TestPreflightJsonOutput:
    """Req 6.3: JSON output is valid and contains expected fields."""

    def test_json_output_valid(self, capsys):
        run_preflight(json_mode=True, output_dir=None)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "verdict" in data
        assert "spec_hash" in data
        assert "reasons" in data
        assert "exit_code" in data
        assert "version" in data
        assert "allowed" in data

    def test_json_verdict_is_block(self, capsys):
        run_preflight(json_mode=True, output_dir=None)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert data["verdict"] == "release_block"
        assert data["exit_code"] == 2
        assert data["allowed"] is False

    def test_json_reasons_contain_missing_data(self, capsys):
        run_preflight(json_mode=True, output_dir=None)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        assert "NO_TIER_DATA" in data["reasons"]
        assert "NO_FLAKE_DATA" in data["reasons"]


class TestPreflightSpecHash:
    """Req 6.4: spec_hash in output is 64-char hex."""

    def test_spec_hash_in_json_output(self, capsys):
        run_preflight(json_mode=True, output_dir=None)
        captured = capsys.readouterr()
        data = json.loads(captured.out)
        h = data["spec_hash"]
        assert len(h) == 64
        int(h, 16)  # valid hex

    def test_spec_hash_in_text_output(self, capsys):
        run_preflight(json_mode=False, output_dir=None)
        captured = capsys.readouterr()
        # Find spec_hash line
        for line in captured.out.splitlines():
            if line.startswith("spec_hash:"):
                h = line.split(":", 1)[1].strip()
                assert len(h) == 64
                int(h, 16)
                return
        pytest.fail("spec_hash not found in text output")


class TestPreflightArtifacts:
    """Req 6.4 extended: --output-dir creates report files."""

    def test_output_dir_creates_files(self, tmp_path):
        exit_code = run_preflight(json_mode=False, output_dir=str(tmp_path))
        assert exit_code == _EXIT_BLOCK

        # Check text file
        txt_files = list(tmp_path.glob("release_preflight_*.txt"))
        assert len(txt_files) == 1

        # Check JSON file
        json_files = list(tmp_path.glob("release_preflight_*.json"))
        assert len(json_files) == 1

        # JSON file is valid
        data = json.loads(json_files[0].read_text(encoding="utf-8"))
        assert "verdict" in data

    def test_artifact_filename_contains_verdict(self, tmp_path):
        run_preflight(output_dir=str(tmp_path))
        json_files = list(tmp_path.glob("*.json"))
        assert any("release_block" in f.name for f in json_files)


class TestExitCodeContract:
    """Exit code mapping is stable and documented."""

    def test_exit_code_constants(self):
        assert _EXIT_OK == 0
        assert _EXIT_HOLD == 1
        assert _EXIT_BLOCK == 2
        assert _EXIT_USAGE == 64
