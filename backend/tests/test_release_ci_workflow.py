"""
PR-16: CI workflow YAML yapısal doğrulama testleri.

Validates: Requirements 1.4, 1.5, 4.1-4.4
"""
import yaml
import pytest
from pathlib import Path

_WORKFLOW_PATH = Path(__file__).resolve().parents[2] / "docs" / "ci" / "release-governance.yml"


@pytest.fixture(scope="module")
def workflow():
    text = _WORKFLOW_PATH.read_text(encoding="utf-8")
    return yaml.safe_load(text)


class TestWorkflowStructure:
    """CI workflow YAML yapısal doğrulama."""

    def test_yaml_parses(self, workflow):
        assert workflow is not None

    def test_workflow_dispatch_exists(self, workflow):
        """Req 4.1: workflow_dispatch event'i mevcut."""
        # YAML 'on' key is parsed as True by PyYAML; use True as key
        on_key = True if True in workflow else "on"
        assert "workflow_dispatch" in workflow[on_key]

    def test_workflow_dispatch_inputs(self, workflow):
        """Req 4.1: override_reason, override_scope, override_by input'ları."""
        on_key = True if True in workflow else "on"
        inputs = workflow[on_key]["workflow_dispatch"]["inputs"]
        assert "override_reason" in inputs
        assert "override_scope" in inputs
        assert "override_by" in inputs

    def test_no_continue_on_error(self, workflow):
        """Req 1.4: preflight adımında continue-on-error yok."""
        preflight_job = workflow["jobs"]["preflight"]
        for step in preflight_job["steps"]:
            if step.get("id") == "preflight":
                assert "continue-on-error" not in step
                return
        pytest.fail("preflight step not found")

    def test_artifact_upload_preserved(self, workflow):
        """Req 1.5: artifact upload adımı korunmuş."""
        preflight_job = workflow["jobs"]["preflight"]
        upload_steps = [
            s for s in preflight_job["steps"]
            if "upload-artifact" in str(s.get("uses", ""))
        ]
        assert len(upload_steps) >= 1

    def test_override_args_in_preflight_run(self, workflow):
        """Req 4.2: override flag'leri preflight run komutunda conditional."""
        preflight_job = workflow["jobs"]["preflight"]
        for step in preflight_job["steps"]:
            if step.get("id") == "preflight":
                run_cmd = step.get("run", "")
                assert "--override-reason" in run_cmd
                assert "--override-scope" in run_cmd
                assert "--override-by" in run_cmd
                return
        pytest.fail("preflight step not found")

    def test_branch_guard_step_exists(self, workflow):
        """Branch guard: override yalnızca main/release/* üzerinde."""
        preflight_job = workflow["jobs"]["preflight"]
        guard_steps = [
            s for s in preflight_job["steps"]
            if "branch guard" in str(s.get("name", "")).lower()
               or "override yalnızca" in str(s.get("run", "")).lower()
        ]
        assert len(guard_steps) >= 1

    def test_step_summary_has_override_info(self, workflow):
        """Req 4.4: step summary'de override bilgisi."""
        preflight_job = workflow["jobs"]["preflight"]
        for step in preflight_job["steps"]:
            if "summary" in str(step.get("name", "")).lower():
                run_cmd = step.get("run", "")
                if "override" in run_cmd.lower():
                    return
        pytest.fail("override info not found in step summary")
