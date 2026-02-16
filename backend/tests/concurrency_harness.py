"""
Concurrency PBT — Harness Utilities.

Shared helpers for parallel SnapshotFactory.build() execution,
deterministic config construction, and tenant constants.

Feature: concurrency-pbt, Task 1
"""
from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import Any

from backend.app.guard_config import GuardConfig
from backend.app.guards.guard_decision import (
    GuardDecisionSnapshot,
    SnapshotFactory,
    TenantMode,
)

# ── Tenant constants ─────────────────────────────────────────────────────────

TENANT_MODES = {"tenantA": "enforce", "tenantB": "shadow", "tenantC": "off"}
TENANT_MODES_JSON = json.dumps(TENANT_MODES)
TENANT_ALLOWLIST_JSON = '["tenantA","tenantB","tenantC"]'
KNOWN_TENANTS = ["tenantA", "tenantB", "tenantC", "tenantX"]

EXPECTED_MODES = {
    "tenantA": TenantMode.ENFORCE,
    "tenantB": TenantMode.SHADOW,
    "tenantC": TenantMode.OFF,
    # tenantX not in map → default_mode (shadow)
}
DEFAULT_MODE = TenantMode.SHADOW


def expected_mode_for(tenant_id: str) -> TenantMode:
    """Return expected TenantMode for a given tenant_id."""
    return EXPECTED_MODES.get(tenant_id, DEFAULT_MODE)


# ── Config factory ───────────────────────────────────────────────────────────

def make_test_config(**overrides: Any) -> GuardConfig:
    """Build a tenant-aware GuardConfig via model_construct (skip env)."""
    defaults = dict(
        schema_version="1.0",
        config_version="test",
        last_updated_at="2026-02-16T00:00:00Z",
        cb_precheck_enabled=False,
        decision_layer_enabled=True,
        decision_layer_mode="enforce",
        decision_layer_default_mode="shadow",
        decision_layer_tenant_modes_json=TENANT_MODES_JSON,
        decision_layer_tenant_allowlist_json=TENANT_ALLOWLIST_JSON,
    )
    defaults.update(overrides)
    return GuardConfig.model_construct(**defaults)


# ── Parallel build ───────────────────────────────────────────────────────────

def parallel_snapshot_builds(
    build_args_list: list[dict],
    max_workers: int = 20,
) -> list[GuardDecisionSnapshot | None]:
    """
    ThreadPoolExecutor ile paralel SnapshotFactory.build() çağrıları.

    Her build bağımsız; sonuçlar input sırasıyla eşleşir.
    Exception → None (fail-open semantics preserved).
    """
    results: list[GuardDecisionSnapshot | None] = [None] * len(build_args_list)

    def _build(idx: int, kwargs: dict) -> tuple[int, GuardDecisionSnapshot | None]:
        try:
            return idx, SnapshotFactory.build(**kwargs)
        except Exception:
            return idx, None

    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = [
            pool.submit(_build, i, args)
            for i, args in enumerate(build_args_list)
        ]
        for future in as_completed(futures):
            idx, snapshot = future.result()
            results[idx] = snapshot

    return results


# ── Async parallel build ─────────────────────────────────────────────────────

import asyncio


async def _async_build(kwargs: dict) -> GuardDecisionSnapshot | None:
    """Run SnapshotFactory.build() in executor (non-blocking)."""
    loop = asyncio.get_running_loop()
    try:
        return await loop.run_in_executor(None, lambda: SnapshotFactory.build(**kwargs))
    except Exception:
        return None


async def _async_parallel_builds(
    build_args_list: list[dict],
) -> list[GuardDecisionSnapshot | None]:
    """asyncio.gather ile paralel build — event loop concurrency."""
    tasks = [_async_build(kwargs) for kwargs in build_args_list]
    return list(await asyncio.gather(*tasks))


def parallel_snapshot_builds_async(
    build_args_list: list[dict],
) -> list[GuardDecisionSnapshot | None]:
    """
    asyncio.gather wrapper — yeni event loop oluşturur.
    Thread-based harness ile aynı arayüz.
    """
    return asyncio.run(_async_parallel_builds(build_args_list))
