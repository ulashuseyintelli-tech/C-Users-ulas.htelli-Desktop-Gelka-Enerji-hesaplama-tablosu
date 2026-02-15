import json
from backend.app.testing.stress_report import StressReport, FailDiagnostic


def test_report_shape_and_required_fields():
    report = StressReport(
        results=[{"scenario_id": "noop"}],
        table=[{"scenario_id": "noop"}],
        fail_summary=[],
        diagnostics=[FailDiagnostic(
            scenario_id="noop",
            dependency="none",
            outcome="success",
            observed=1,
            expected=1,
            seed=1337
        )],
        metadata={"version": 1},
    )
    s = report.to_json()
    payload = json.loads(s)
    assert "results" in payload
    assert "table" in payload
    assert "fail_summary" in payload
    assert "diagnostics" in payload
    assert len(payload["table"]) == len(payload["results"])
    # FailDiagnostic schema
    d0 = payload["diagnostics"][0]
    for k in ["scenario_id", "dependency", "outcome", "observed", "expected", "seed"]:
        assert k in d0
