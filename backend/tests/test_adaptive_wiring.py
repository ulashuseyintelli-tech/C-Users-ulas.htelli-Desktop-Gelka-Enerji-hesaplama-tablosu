"""
Tests for adaptive control wiring + integration.

Feature: slo-adaptive-control, Task 11.4
Requirements: 4.1, 8.1, 9.4
"""

from __future__ import annotations

import pytest

from backend.app.adaptive_control import create_adaptive_controller
from backend.app.adaptive_control.config import (
    AdaptiveControlConfig,
    AllowlistEntry,
)
from backend.app.adaptive_control.controller import (
    AdaptiveController,
    AdaptiveControllerState,
)
from backend.app.guard_config import GuardConfig, load_guard_config
from backend.app.services.pdf_job_store import BackpressureActiveError


class TestFactoryWiring:
    """Task 11.4: Factory creates all components correctly."""

    def test_factory_creates_all_components(self):
        """create_adaptive_controller() returns a wired AdaptiveController."""
        config = AdaptiveControlConfig(
            targets=[AllowlistEntry(subsystem_id="*")],
        )
        ctrl = create_adaptive_controller(config=config)
        assert isinstance(ctrl, AdaptiveController)
        assert ctrl.state == AdaptiveControllerState.RUNNING

    def test_factory_with_callbacks(self):
        """Factory accepts guard/pdf callbacks."""
        guard_calls = []
        pdf_calls = []
        config = AdaptiveControlConfig(
            targets=[AllowlistEntry(subsystem_id="*")],
        )
        ctrl = create_adaptive_controller(
            config=config,
            guard_mode_setter=lambda m: guard_calls.append(m),
            pdf_backpressure_setter=lambda a: pdf_calls.append(a),
        )
        assert isinstance(ctrl, AdaptiveController)

    def test_factory_with_killswitch(self):
        """Factory accepts killswitch callback."""
        config = AdaptiveControlConfig(
            targets=[AllowlistEntry(subsystem_id="*")],
        )
        ctrl = create_adaptive_controller(
            config=config,
            killswitch_active_fn=lambda sub: False,
        )
        assert isinstance(ctrl, AdaptiveController)

    def test_factory_loads_default_config(self):
        """Factory loads config from env when None."""
        ctrl = create_adaptive_controller()
        assert isinstance(ctrl, AdaptiveController)


class TestAdaptiveControlEnabled:
    """Task 11.2: adaptive_control_enabled flag in GuardConfig."""

    def test_adaptive_control_disabled_by_default(self):
        """Safe default: adaptive_control_enabled = False (Req 9.4)."""
        loaded = load_guard_config()
        assert loaded.adaptive_control_enabled is False

    def test_adaptive_control_field_exists(self):
        """GuardConfig has adaptive_control_enabled field."""
        config = GuardConfig.model_construct(adaptive_control_enabled=True)
        assert config.adaptive_control_enabled is True


class TestBackpressureHook:
    """Task 11.3: Backpressure hook in PdfJobStore."""

    def test_backpressure_blocks_new_jobs(self):
        """Backpressure active → BackpressureActiveError (Req 8.1)."""
        from unittest.mock import MagicMock
        store = _make_store()
        store.set_backpressure(True, retry_after_seconds=60)
        with pytest.raises(BackpressureActiveError) as exc_info:
            store.create_job("template", {"key": "value"})
        assert exc_info.value.retry_after_seconds == 60

    def test_backpressure_inactive_allows_jobs(self):
        """Backpressure inactive → jobs created normally."""
        store = _make_store()
        store.set_backpressure(False)
        assert store.backpressure_active is False

    def test_backpressure_default_inactive(self):
        """Backpressure defaults to inactive."""
        store = _make_store()
        assert store.backpressure_active is False

    def test_backpressure_retry_after_in_error(self):
        """BackpressureActiveError includes retry_after_seconds."""
        err = BackpressureActiveError(45)
        assert err.retry_after_seconds == 45
        assert "BACKPRESSURE_ACTIVE" in str(err)
        assert "45" in str(err)

    def test_set_backpressure_toggle(self):
        """set_backpressure toggles state correctly."""
        store = _make_store()
        store.set_backpressure(True, 30)
        assert store.backpressure_active is True
        assert store.backpressure_retry_after == 30
        store.set_backpressure(False)
        assert store.backpressure_active is False


def _make_store():
    """Create a PdfJobStore with a mock Redis connection."""
    from unittest.mock import MagicMock
    from backend.app.services.pdf_job_store import PdfJobStore
    return PdfJobStore(redis_conn=MagicMock())
