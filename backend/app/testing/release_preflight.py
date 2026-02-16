"""
PR-15/16: Release Preflight CLI — thin wrapper over the release governance chain.

Runs: ReleasePolicy → ReleaseReportGenerator → ReleaseGate → stdout/file output.

Usage:
    python -m backend.app.testing.release_preflight [--json] [--output-dir DIR]
    python -m backend.app.testing.release_preflight --override-reason "..." --override-scope "..." --override-by "..."

Exit codes:
    0  = RELEASE_OK  (or HOLD with valid override)
    1  = RELEASE_HOLD (no override or invalid override)
    2  = RELEASE_BLOCK (always — override never changes this)
    64 = usage error (bad arguments)

Override rules:
    - All three flags required (--override-reason, --override-scope, --override-by)
    - Partial flags → override ignored, normal flow
    - HOLD + valid override → exit 0
    - BLOCK + override → exit 2 (CONTRACT_BREACH if ABSOLUTE_BLOCK_REASONS)
    - OK + override → override ignored, exit 0

Stdout: machine-friendly summary (verdict, spec_hash, reasons, report path).
Stderr: human-readable diagnostics only.
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from backend.app.testing.policy_engine import AuditLog, OpsGateStatus
from backend.app.testing.release_gate import ReleaseGate, ReleaseOverride
from backend.app.testing.release_policy import (
    ABSOLUTE_BLOCK_REASONS,
    ReleasePolicy,
    ReleasePolicyInput,
    ReleaseVerdict,
)
from backend.app.testing.release_report import ReleaseReportGenerator
from backend.app.testing.release_version import VERSION, spec_hash

# Exit code contract
_EXIT_OK = 0
_EXIT_HOLD = 1
_EXIT_BLOCK = 2
_EXIT_USAGE = 64

_VERDICT_EXIT: dict[str, int] = {
    ReleaseVerdict.RELEASE_OK.value: _EXIT_OK,
    ReleaseVerdict.RELEASE_HOLD.value: _EXIT_HOLD,
    ReleaseVerdict.RELEASE_BLOCK.value: _EXIT_BLOCK,
}

# Override TTL for CI (1 hour)
_OVERRIDE_TTL_SECONDS = 3600


def _now_ms() -> int:
    """Current time in milliseconds."""
    return int(time.time() * 1000)


def _build_dry_run_input() -> ReleasePolicyInput:
    """Minimal input with no signal data — expected to produce BLOCK."""
    return ReleasePolicyInput(
        tier_results=[],
        flake_snapshot=None,
        drift_snapshot=None,
        canary_result=None,
        ops_gate=OpsGateStatus(passed=True),
    )


def _build_override(
    reason: str | None,
    scope: str | None,
    created_by: str | None,
) -> ReleaseOverride | None:
    """Build ReleaseOverride if all three flags provided, else None."""
    if not all([reason, scope, created_by]):
        return None
    return ReleaseOverride(
        ttl_seconds=_OVERRIDE_TTL_SECONDS,
        created_at_ms=_now_ms(),
        scope=scope,  # type: ignore[arg-type]
        reason=reason,  # type: ignore[arg-type]
        created_by=created_by,  # type: ignore[arg-type]
    )


def run_preflight(
    json_mode: bool = False,
    output_dir: str | None = None,
    override_reason: str | None = None,
    override_scope: str | None = None,
    override_by: str | None = None,
    metrics_dir: str | None = None,
) -> int:
    """
    Execute the release governance chain and produce output.

    Returns exit code (0/1/2).
    """
    t0 = time.perf_counter()

    # 1. Build input (dry-run: no real signals)
    inp = _build_dry_run_input()

    # 2. Policy evaluation
    policy = ReleasePolicy()
    result = policy.evaluate(inp)

    # 3. Report generation
    gen = ReleaseReportGenerator()
    now_iso = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
    report = gen.generate(result, inp, generated_at=now_iso)

    # 4. Gate check (initial — no override)
    now_ms = _now_ms()
    audit = AuditLog()
    gate = ReleaseGate(audit_log=audit)
    decision = gate.check(result, release_scope="preflight", now_ms=now_ms)

    # 5. Override logic (PR-16)
    override = _build_override(override_reason, override_scope, override_by)
    override_applied = False
    contract_breach = False
    contract_breach_detail: str | None = None

    if override is not None:
        if result.verdict == ReleaseVerdict.RELEASE_HOLD:
            # Re-check gate with override
            decision = gate.check(
                result, override=override,
                release_scope="preflight", now_ms=now_ms,
            )
            if decision.allowed:
                override_applied = True
        elif result.verdict == ReleaseVerdict.RELEASE_BLOCK:
            # Check for absolute block — CONTRACT_BREACH
            has_absolute = bool(
                set(result.reasons) & ABSOLUTE_BLOCK_REASONS
            )
            if has_absolute:
                contract_breach = True
                breach_codes = [
                    r.value for r in result.reasons
                    if r in ABSOLUTE_BLOCK_REASONS
                ]
                contract_breach_detail = (
                    f"CONTRACT_BREACH_NO_OVERRIDE: {', '.join(breach_codes)}"
                )
            # BLOCK exit code stays 2 regardless
        # verdict OK → override ignored (unnecessary)

    # 6. Spec hash
    try:
        current_hash = spec_hash()
    except Exception:
        current_hash = "unavailable"

    # 7. Build exit code
    reasons_list = [r.value for r in result.reasons]
    if override_applied:
        exit_code = _EXIT_OK
    else:
        exit_code = _VERDICT_EXIT.get(result.verdict.value, _EXIT_BLOCK)

    report_text = gen.format_text(report)
    report_dict = gen.to_dict(report)

    # 8. Write artifacts if output_dir specified
    report_path: str | None = None
    if output_dir:
        out = Path(output_dir)
        out.mkdir(parents=True, exist_ok=True)

        verdict_tag = result.verdict.value
        text_file = out / f"release_preflight_{verdict_tag}.txt"
        json_file = out / f"release_preflight_{verdict_tag}.json"

        text_file.write_text(report_text, encoding="utf-8")
        json_file.write_text(
            json.dumps(report_dict, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        report_path = str(json_file)

    # 9. Output to stdout
    output: dict[str, Any] = {
        "verdict": result.verdict.value,
        "exit_code": exit_code,
        "spec_hash": current_hash,
        "version": VERSION,
        "reasons": reasons_list,
        "allowed": decision.allowed,
        "override_applied": override_applied,
        "contract_breach": contract_breach,
    }
    if override_applied and override_by:
        output["override_by"] = override_by
        output["override_reason"] = override_reason
    if contract_breach_detail:
        output["contract_breach_detail"] = contract_breach_detail
    if report_path:
        output["report_path"] = report_path

    if json_mode:
        sys.stdout.write(json.dumps(output, indent=2, ensure_ascii=False) + "\n")
    else:
        sys.stdout.write(f"verdict:   {output['verdict']}\n")
        sys.stdout.write(f"spec_hash: {output['spec_hash']}\n")
        sys.stdout.write(f"version:   {output['version']}\n")
        sys.stdout.write(f"reasons:   {', '.join(reasons_list) if reasons_list else '(none)'}\n")
        sys.stdout.write(f"allowed:   {output['allowed']}\n")
        if override_applied:
            sys.stdout.write(f"override:  applied by {override_by}\n")
        if contract_breach:
            sys.stdout.write(f"breach:    {contract_breach_detail}\n")
        if report_path:
            sys.stdout.write(f"report:    {report_path}\n")

    # 10. Metrics export (PR-17) — fail-open
    if metrics_dir:
        try:
            from backend.app.testing.preflight_metrics import (
                MetricExporter,
                MetricStore,
                _atomic_write,
            )
            duration_ms = (time.perf_counter() - t0) * 1000
            metric = MetricExporter.from_preflight_output(output, duration_ms=duration_ms)
            store = MetricStore()
            store.load_from_dir(metrics_dir)
            store.add(metric)
            store.save_to_dir(metrics_dir)
            # Prometheus text exposition (latest snapshot)
            prom_path = Path(metrics_dir) / "preflight.prom"
            _atomic_write(prom_path, MetricExporter.export_prometheus(store))
        except Exception as exc:
            sys.stderr.write(
                f"[preflight-metrics] Uyarı: metrik yazılamadı: {exc}\n"
            )
            # Fail-open: exit code değişmez

    return exit_code


def main() -> None:
    parser = argparse.ArgumentParser(
        prog="release_preflight",
        description="Release Governance preflight check.",
    )
    parser.add_argument(
        "--json", action="store_true", dest="json_mode",
        help="Output in JSON format (machine-readable).",
    )
    parser.add_argument(
        "--output-dir", type=str, default=None,
        help="Directory to write report artifacts (text + JSON).",
    )
    parser.add_argument(
        "--override-reason", type=str, default=None,
        help="HOLD override reason (all three override flags required).",
    )
    parser.add_argument(
        "--override-scope", type=str, default=None,
        help="HOLD override scope / release identifier.",
    )
    parser.add_argument(
        "--override-by", type=str, default=None,
        help="HOLD override approver (username/handle, not email).",
    )
    parser.add_argument(
        "--metrics-dir", type=str, default=None,
        help="Directory to write telemetry metrics (JSON + Prometheus).",
    )

    try:
        args = parser.parse_args()
    except SystemExit as e:
        sys.exit(_EXIT_USAGE if e.code != 0 else 0)

    exit_code = run_preflight(
        json_mode=args.json_mode,
        output_dir=args.output_dir,
        override_reason=args.override_reason,
        override_scope=args.override_scope,
        override_by=args.override_by,
        metrics_dir=args.metrics_dir,
    )
    sys.exit(exit_code)


if __name__ == "__main__":
    main()
