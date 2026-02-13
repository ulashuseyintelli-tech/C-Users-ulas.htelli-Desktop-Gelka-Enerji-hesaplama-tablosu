"""
Integration tests for OpsGuardMiddleware (no-op skeleton).

Verifies middleware is active but does not change behavior.

Feature: ops-guard, Task 2.3
"""

import os
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture()
def client():
    """TestClient with admin-key bypassed."""
    with patch.dict(os.environ, {"ADMIN_API_KEY_ENABLED": "false", "API_KEY_ENABLED": "false"}):
        from app.main import app as fastapi_app
        from app.database import get_db
        from fastapi.testclient import TestClient

        mock_db = MagicMock()
        fastapi_app.dependency_overrides[get_db] = lambda: mock_db

        yield TestClient(fastapi_app)

        fastapi_app.dependency_overrides.clear()


class TestOpsGuardMiddlewareNoOp:
    """Middleware active but no behavioral change."""

    def test_health_endpoint_still_works(self, client):
        """Health endpoint should return 200."""
        resp = client.get("/health")
        assert resp.status_code == 200

    def test_metrics_endpoint_still_works(self, client):
        """Metrics endpoint should return 200 with prometheus output."""
        resp = client.get("/metrics")
        assert resp.status_code == 200
        assert b"ptf_admin_" in resp.content

    def test_unknown_endpoint_returns_404(self, client):
        """Unknown path should still 404."""
        resp = client.get("/nonexistent-path-xyz")
        assert resp.status_code in (404, 405)

    def test_middleware_does_not_block_admin_endpoints(self, client):
        """Admin endpoints should still be reachable (not blocked by guard)."""
        # Use deprecation-stats which doesn't need DB
        resp = client.get("/admin/market-prices/deprecation-stats")
        # Should NOT be 503 from guard — any other status is fine
        assert resp.status_code != 503
