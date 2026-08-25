"""
Shared test configuration for backend tests.

Hypothesis settings:
- CI profile disables example database to prevent "Flaky" errors from stale examples
- Default profile keeps database for local development

Import path fix:
- Production code uses two import styles: `from app.` and `from backend.app.`
- `from backend.app.` needs the repo root on sys.path
- `from app.` needs the backend/ dir itself on sys.path
- We add both here so both styles resolve deterministically, regardless of
  which test file pytest happens to collect first.

Why this matters (PDSMR follow-up, 2026-08-18):
- backend/__init__.py and backend/tests/__init__.py make `backend` and
  `backend.tests` real packages, so pytest's own "prepend" import-mode
  rootpath walk (see _pytest.pathlib.resolve_package_path) climbs past
  backend/ and only ever plants the repo root on sys.path[0] for test
  modules under here — never backend/ itself.
- Without the explicit backend/ entry below, bare `from app...` imports
  only worked by accident: some test file alphabetically before the
  failing ones (e.g. test_api_properties.py) happened to do its own
  `sys.path.insert(0, ...)` of backend/ as a private workaround, which
  incidentally primed sys.modules['app'] for every file collected after
  it. Any file collected earlier (alphabetically) than that accident,
  such as test_action_hints_golden.py / test_action_hints_unit.py, hit
  `ModuleNotFoundError: No module named 'app'` during collection.
- We append (not insert at position 0) so this can never shadow a real
  same-named top-level package already resolvable earlier on sys.path
  (e.g. the real `alembic` library vs. backend/alembic/, which is also
  a package thanks to backend/alembic/__init__.py).
"""

import sys
from pathlib import Path

# Add repo root (parent of backend/) to sys.path so `from backend.app...` works
_repo_root = str(Path(__file__).resolve().parent.parent.parent)
if _repo_root not in sys.path:
    sys.path.insert(0, _repo_root)

# Add backend/ itself to sys.path so bare `from app...` works deterministically,
# not just when some other test file happens to have added it first.
# Appended (never inserted at the front) so it can't shadow a real package of
# the same name (e.g. the `alembic` library) that resolves earlier on sys.path.
_backend_root = str(Path(__file__).resolve().parent.parent)
if _backend_root not in sys.path:
    sys.path.append(_backend_root)

from hypothesis import settings, HealthCheck

# S5-R02A: `deadline=None` + `derandomize=True`
#
# deadline=None — DOGRULUK DUVAR SAATINDEN AYRILDI (owner Bolum 7).
#   Hypothesis'in varsayilan 200ms deadline'i her ORNEGIN duvar saatini
#   olcer. Tam regresyonun CPU rekabeti altinda hangi ornegin 200ms'i
#   astigi kosudan kosuya degisiyordu; "gezinen" hatalarin mekanizmasi
#   buydu (olculen ornek: 253.43ms > 200ms). Deadline bir dogruluk
#   assertion'i DEGILDIR; kaldirilmasi hicbir assertion'i gevsetmez.
#   Gercek performans olcumu ayri benchmark sozlesmesinin isidir.
#
# derandomize=True — AYNI girdi kumesi her kosuda AYNI ornekleri uretir;
#   uc ardisik tam regresyonun ayni test kimligi kumesinde ayni sonucu
#   vermesi boylece mekanik olarak karsilastirilabilir. Ornek cesitliligi
#   kaybolmaz (strateji uzayi ayni), yalniz kosular arasi kararsizlik
#   kalkar.
settings.register_profile(
    "ci",
    database=None,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)

# Default profile: ayni sozlesme (bkz. yukaridaki gerekce)
settings.register_profile(
    "default",
    database=None,
    deadline=None,
    derandomize=True,
    suppress_health_check=[HealthCheck.too_slow],
)

settings.load_profile("default")


# ── PR-10: Test tier markers ──────────────────────────────────────────────────
# Usage: pytest -m smoke, pytest -m core, pytest -m concurrency
# These are registered to avoid PytestUnknownMarkWarning.

def pytest_configure(config):
    config.addinivalue_line("markers", "smoke: Tier-0 pure-math + config tests (<10s)")
    config.addinivalue_line("markers", "core: Tier-1 core logic + stores (<15s)")
    config.addinivalue_line("markers", "concurrency: Tier-2 thread races (<30s)")
    config.addinivalue_line("markers", "soak: Tier-3 large PBT / nightly (<120s)")


# ── Ops-Guard singleton isolation ─────────────────────────────────────────────
# Rate limiter and kill-switch singletons are module-level; without reset,
# tests that share the same process accumulate state (e.g. rate limit buckets
# fill up across test files). This autouse fixture resets them before each test.

import pytest


@pytest.fixture(autouse=True)
def _reset_ops_guard_singletons():
    """Reset ops-guard singletons before each test for isolation."""
    try:
        import app.ops_guard_middleware as ogm
        if ogm._rate_limit_guard is not None:
            ogm._rate_limit_guard.reset()
    except Exception:
        pass

    try:
        import app.main as main_mod
        if main_mod._kill_switch_manager is not None:
            # Re-init from config (resets switch states)
            pass  # kill-switch state is test-managed, don't auto-reset
    except Exception:
        pass

    yield

    # Post-test cleanup
    try:
        import app.ops_guard_middleware as ogm
        if ogm._rate_limit_guard is not None:
            ogm._rate_limit_guard.reset()
    except Exception:
        pass
