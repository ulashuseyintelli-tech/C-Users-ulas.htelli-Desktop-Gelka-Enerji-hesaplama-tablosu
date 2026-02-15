import importlib


def test_eval_interval_env_fallback_60(monkeypatch):
    monkeypatch.delenv("EVAL_INTERVAL_SECONDS", raising=False)
    # Reload module to apply env changes
    mod = importlib.import_module("backend.app.testing.lc_config")
    importlib.reload(mod)
    assert mod.EVAL_INTERVAL_SECONDS == 60


def test_gnk3_min_requests_present():
    from backend.app.testing.lc_config import MIN_REQUESTS_BY_PROFILE, ProfileType
    assert MIN_REQUESTS_BY_PROFILE[ProfileType.BASELINE] >= 200
    assert MIN_REQUESTS_BY_PROFILE[ProfileType.STRESS] >= 500
