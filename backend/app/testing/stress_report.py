from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field
from typing import Any, Optional


@dataclass(frozen=True)
class FailDiagnostic:
    scenario_id: str
    dependency: str
    outcome: str
    observed: Any
    expected: Any
    seed: int


@dataclass(frozen=True)
class FlakyCorrelationSegment:
    timing_deviation_ms: float
    suspected_source: str
    repro_steps: str


@dataclass(frozen=True)
class TuningRecommendation:
    kind: str
    reason: str
    details: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class StressReport:
    results: list[dict[str, Any]]
    table: list[dict[str, Any]]
    fail_summary: list[dict[str, Any]]
    diagnostics: list[FailDiagnostic] = field(default_factory=list)
    flaky_segment: Optional[dict[str, Any]] = None
    metadata: dict[str, Any] = field(default_factory=dict)
    tuning_recommendations: list[TuningRecommendation] = field(default_factory=list)

    def to_json(self) -> str:
        # Deterministic JSON output (stable ordering)
        payload = asdict(self)
        return json.dumps(payload, sort_keys=True, separators=(",", ":"))
