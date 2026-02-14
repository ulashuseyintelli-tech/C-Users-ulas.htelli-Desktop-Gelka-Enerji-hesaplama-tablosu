"""
Kill-Switch Manager — global, per-tenant, and degrade mode controls.

Hard mode: endpoint disabled → HTTP 503 + deterministic error code.
Soft mode (degrade): write path closed, read allowed.

Failure semantics (HD-1):
  - High-risk endpoints (import/apply, bulk write): fail-closed (503)
  - All other endpoints: fail-open (request passes) + metric + log

Audit log format: [KILLSWITCH] actor={actor} switch={name} old={old} new={new} timestamp={ts}

Feature: ops-guard, Task 4.1
"""

import logging
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Optional

from .guard_config import GuardConfig, GuardDenyReason
from .ptf_metrics import PTFMetrics

logger = logging.getLogger(__name__)


@dataclass
class KillSwitchEntry:
    """Immutable snapshot of a kill-switch state."""
    name: str
    enabled: bool
    updated_at: str = ""
    updated_by: str = "system"


class KillSwitchManager:
    """
    In-memory kill-switch state manager.

    Switch names (HD-5 bounded set):
      - global_import: disables all import endpoints
      - degrade_mode: disables write path, read-only mode
      - tenant:{id}: disables specific tenant's import
    """

    def __init__(self, config: GuardConfig, metrics: PTFMetrics) -> None:
        self._config = config
        self._metrics = metrics
        self._switches: dict[str, KillSwitchEntry] = {}
        self._init_from_config()

    def _init_from_config(self) -> None:
        """Initialize switch states from GuardConfig defaults."""
        now = datetime.now(timezone.utc).isoformat()

        self._switches["global_import"] = KillSwitchEntry(
            name="global_import",
            enabled=self._config.killswitch_global_import_disabled,
            updated_at=now,
            updated_by="config",
        )
        self._switches["degrade_mode"] = KillSwitchEntry(
            name="degrade_mode",
            enabled=self._config.killswitch_degrade_mode,
            updated_at=now,
            updated_by="config",
        )

        # Per-tenant switches from comma-separated config
        disabled_tenants = self._config.killswitch_disabled_tenants
        if disabled_tenants.strip():
            for tenant_id in disabled_tenants.split(","):
                tid = tenant_id.strip()
                if tid:
                    key = f"tenant:{tid}"
                    self._switches[key] = KillSwitchEntry(
                        name=key, enabled=True, updated_at=now, updated_by="config",
                    )

        # Emit initial gauge values
        for name, entry in self._switches.items():
            self._metrics.set_killswitch_state(name, entry.enabled)

    # ── Query methods ─────────────────────────────────────────────────────

    def is_import_disabled(self, tenant_id: Optional[str] = None) -> bool:
        """Check if import is disabled globally or for a specific tenant."""
        if self._switches.get("global_import", KillSwitchEntry("global_import", False)).enabled:
            return True
        if tenant_id:
            key = f"tenant:{tenant_id}"
            entry = self._switches.get(key)
            if entry and entry.enabled:
                return True
        return False

    def is_degrade_mode(self) -> bool:
        """Check if degrade mode (read-only) is active."""
        return self._switches.get("degrade_mode", KillSwitchEntry("degrade_mode", False)).enabled

    def get_disabled_tenants(self) -> set[str]:
        """Return set of disabled tenant IDs."""
        result = set()
        for name, entry in self._switches.items():
            if name.startswith("tenant:") and entry.enabled:
                result.add(name.removeprefix("tenant:"))
        return result

    def get_all_switches(self) -> dict[str, dict]:
        """Return all switch states as serializable dict."""
        return {
            name: {
                "name": entry.name,
                "enabled": entry.enabled,
                "updated_at": entry.updated_at,
                "updated_by": entry.updated_by,
            }
            for name, entry in self._switches.items()
        }

    # ── Mutation ──────────────────────────────────────────────────────────

    def set_switch(self, switch_name: str, enabled: bool, actor: str) -> dict:
        """
        Set kill-switch state. Creates switch if it doesn't exist (for tenant switches).

        Returns the updated switch entry as dict.
        Emits audit log + metric gauge update.
        """
        now = datetime.now(timezone.utc).isoformat()
        old_entry = self._switches.get(switch_name)
        old_enabled = old_entry.enabled if old_entry else False

        new_entry = KillSwitchEntry(
            name=switch_name,
            enabled=enabled,
            updated_at=now,
            updated_by=actor,
        )
        self._switches[switch_name] = new_entry

        # Audit log
        logger.info(
            f"[KILLSWITCH] actor={actor} switch={switch_name} "
            f"old={old_enabled} new={enabled} timestamp={now}"
        )

        # Metric gauge update
        self._metrics.set_killswitch_state(switch_name, enabled)

        return {
            "name": switch_name,
            "enabled": enabled,
            "updated_at": now,
            "updated_by": actor,
            "previous_enabled": old_enabled,
        }

    # ── Guard decision ────────────────────────────────────────────────────

    def check_request(
        self,
        endpoint_template: str,
        method: str,
        is_high_risk: bool,
        tenant_id: Optional[str] = None,
    ) -> Optional[GuardDenyReason]:
        """
        Check if request should be denied by kill-switch.

        Returns None if allowed, GuardDenyReason if denied.
        Implements HD-1 failure semantics for internal errors.
        """
        try:
            # Global import kill-switch
            if self.is_import_disabled(tenant_id):
                if "/import/" in endpoint_template or endpoint_template.endswith("/import"):
                    return GuardDenyReason.KILL_SWITCHED

            # Degrade mode: block write methods
            if self.is_degrade_mode() and method in ("POST", "PUT", "DELETE", "PATCH"):
                return GuardDenyReason.KILL_SWITCHED

            return None  # ALLOW

        except Exception as exc:
            error_type = type(exc).__name__
            endpoint_class = "high_risk" if is_high_risk else "standard"

            logger.error(
                f"[KILLSWITCH] Internal error: {exc}, "
                f"endpoint_class={endpoint_class}, error_type={error_type}"
            )
            self._metrics.inc_killswitch_error(endpoint_class, error_type)

            if is_high_risk:
                # HD-1: fail-closed for high-risk
                return GuardDenyReason.INTERNAL_ERROR
            else:
                # HD-1: fail-open for standard
                self._metrics.inc_killswitch_fallback_open()
                return None
