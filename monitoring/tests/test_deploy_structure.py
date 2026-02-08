"""
Structural validation tests for Kustomize deploy output.

Feature: deploy-integration
"""

import pathlib
import shutil
import subprocess

import pytest
import yaml

MONITORING_ROOT = pathlib.Path(__file__).resolve().parent.parent
OVERLAY_PATH = MONITORING_ROOT / "deploy" / "overlays" / "production"

# Detect kustomize availability: prefer standalone, fallback to kubectl
_KUSTOMIZE_BIN = shutil.which("kustomize")
_KUBECTL_BIN = shutil.which("kubectl")


def _build_kustomize():
    """Run kustomize build and return parsed YAML documents."""
    if _KUSTOMIZE_BIN:
        cmd = [_KUSTOMIZE_BIN, "build", str(OVERLAY_PATH), "--load-restrictor=LoadRestrictionsNone"]
    elif _KUBECTL_BIN:
        cmd = [_KUBECTL_BIN, "kustomize", str(OVERLAY_PATH), "--load-restrictor=LoadRestrictionsNone"]
    else:
        pytest.skip("Neither kustomize nor kubectl found — skipping deploy tests")
        return []

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        pytest.fail(f"kustomize build failed:\n{result.stderr}")

    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


@pytest.fixture(scope="module")
def kustomize_docs():
    return _build_kustomize()


def _find_resource(docs, kind, name=None):
    for doc in docs:
        if doc.get("kind") == kind:
            if name is None or doc.get("metadata", {}).get("name") == name:
                return doc
    return None


class TestKustomizeBuild:
    """Verify kustomize build succeeds and produces expected resources."""

    def test_build_produces_documents(self, kustomize_docs):
        assert len(kustomize_docs) >= 2, "Expected at least ConfigMap + PrometheusRule"

    def test_configmap_exists(self, kustomize_docs):
        cm = _find_resource(kustomize_docs, "ConfigMap", "ptf-admin-dashboard")
        assert cm is not None, "ConfigMap ptf-admin-dashboard not found"

    def test_prometheusrule_exists(self, kustomize_docs):
        pr = _find_resource(kustomize_docs, "PrometheusRule", "ptf-admin-alerts")
        assert pr is not None, "PrometheusRule ptf-admin-alerts not found"


class TestConfigMap:
    """Validate ConfigMap structure and labels."""

    def _get_cm(self, kustomize_docs):
        cm = _find_resource(kustomize_docs, "ConfigMap", "ptf-admin-dashboard")
        assert cm is not None
        return cm

    def test_grafana_dashboard_label(self, kustomize_docs):
        """Validates: Requirements 2.1"""
        cm = self._get_cm(kustomize_docs)
        labels = cm.get("metadata", {}).get("labels", {})
        assert labels.get("grafana_dashboard") == "1"

    def test_grafana_folder_annotation(self, kustomize_docs):
        """Validates: Requirements 2.2"""
        cm = self._get_cm(kustomize_docs)
        annotations = cm.get("metadata", {}).get("annotations", {})
        assert annotations.get("grafana_folder") == "PTF Admin"

    def test_dashboard_json_key(self, kustomize_docs):
        """Validates: Requirements 2.1"""
        cm = self._get_cm(kustomize_docs)
        data = cm.get("data", {})
        assert "ptf-admin-dashboard.json" in data

    def test_namespace(self, kustomize_docs):
        """Validates: Requirements 1.2"""
        cm = self._get_cm(kustomize_docs)
        assert cm["metadata"].get("namespace") == "monitoring"

    def test_common_label(self, kustomize_docs):
        """Validates: Requirements 1.1"""
        cm = self._get_cm(kustomize_docs)
        labels = cm.get("metadata", {}).get("labels", {})
        assert labels.get("app.kubernetes.io/part-of") == "ptf-admin-monitoring"


class TestPrometheusRule:
    """Validate PrometheusRule in kustomize output."""

    def _get_pr(self, kustomize_docs):
        pr = _find_resource(kustomize_docs, "PrometheusRule", "ptf-admin-alerts")
        assert pr is not None
        return pr

    def test_namespace(self, kustomize_docs):
        """Validates: Requirements 1.2"""
        pr = self._get_pr(kustomize_docs)
        assert pr["metadata"].get("namespace") == "monitoring"

    def test_common_label(self, kustomize_docs):
        """Validates: Requirements 1.1"""
        pr = self._get_pr(kustomize_docs)
        labels = pr.get("metadata", {}).get("labels", {})
        assert labels.get("app.kubernetes.io/part-of") == "ptf-admin-monitoring"

    def test_prometheus_label_preserved(self, kustomize_docs):
        """Validates: Requirements 3.1"""
        pr = self._get_pr(kustomize_docs)
        labels = pr.get("metadata", {}).get("labels", {})
        assert labels.get("prometheus") == "kube-prometheus"

    def test_alert_count(self, kustomize_docs):
        pr = self._get_pr(kustomize_docs)
        rules = pr["spec"]["groups"][0]["rules"]
        assert len(rules) == 9
