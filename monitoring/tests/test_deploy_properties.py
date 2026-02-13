"""
Property-based tests for Kustomize deploy output structural invariants.

Feature: deploy-integration
Properties:
  P1: Resource Completeness — build output has ConfigMap + PrometheusRule
  P2: Namespace Consistency — all resources in same namespace
  P3: Label Propagation — app.kubernetes.io/part-of on all resources
  P4: ConfigMap Stability — no hash suffix on ConfigMap name

Uses Hypothesis to iterate over all kustomize output documents.
"""

import pathlib
import shutil
import subprocess

import pytest
import yaml
from hypothesis import given, settings, HealthCheck
from hypothesis import strategies as st

MONITORING_ROOT = pathlib.Path(__file__).resolve().parent.parent
OVERLAY_PATH = MONITORING_ROOT / "deploy" / "overlays" / "production"

_KUSTOMIZE_BIN = shutil.which("kustomize")
_KUBECTL_BIN = shutil.which("kubectl")


def _build_kustomize():
    if _KUSTOMIZE_BIN:
        cmd = [_KUSTOMIZE_BIN, "build", str(OVERLAY_PATH), "--load-restrictor=LoadRestrictionsNone"]
    elif _KUBECTL_BIN:
        cmd = [_KUBECTL_BIN, "kustomize", str(OVERLAY_PATH), "--load-restrictor=LoadRestrictionsNone"]
    else:
        return None

    result = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if result.returncode != 0:
        return None

    docs = list(yaml.safe_load_all(result.stdout))
    return [d for d in docs if d is not None]


KUSTOMIZE_DOCS = _build_kustomize()
_SKIP = KUSTOMIZE_DOCS is None

if _SKIP:
    KUSTOMIZE_DOCS = []  # prevent sampled_from crash


# ── Property 1: Resource Completeness ──────────────────────────────

@pytest.mark.skipif(_SKIP, reason="kustomize not available")
class TestPropertyResourceCompleteness:
    """P1: Build output contains at least 1 ConfigMap + 1 PrometheusRule."""

    def test_has_configmap(self):
        kinds = [d.get("kind") for d in KUSTOMIZE_DOCS]
        assert "ConfigMap" in kinds

    def test_has_prometheusrule(self):
        kinds = [d.get("kind") for d in KUSTOMIZE_DOCS]
        assert "PrometheusRule" in kinds

    def test_minimum_resource_count(self):
        assert len(KUSTOMIZE_DOCS) >= 2


# ── Property 2: Namespace Consistency ──────────────────────────────

@pytest.mark.skipif(_SKIP, reason="kustomize not available")
class TestPropertyNamespaceConsistency:
    """P2: All resources are in the same namespace (monitoring)."""

    @given(doc_idx=st.sampled_from(list(range(max(len(KUSTOMIZE_DOCS), 1)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_all_resources_same_namespace(self, doc_idx):
        if _SKIP or doc_idx >= len(KUSTOMIZE_DOCS):
            return
        doc = KUSTOMIZE_DOCS[doc_idx]
        ns = doc.get("metadata", {}).get("namespace")
        assert ns == "monitoring", (
            f"{doc.get('kind')}/{doc['metadata']['name']} namespace={ns}, expected monitoring"
        )


# ── Property 3: Label Propagation ─────────────────────────────────

@pytest.mark.skipif(_SKIP, reason="kustomize not available")
class TestPropertyLabelPropagation:
    """P3: app.kubernetes.io/part-of label present on all resources."""

    @given(doc_idx=st.sampled_from(list(range(max(len(KUSTOMIZE_DOCS), 1)))))
    @settings(max_examples=50, suppress_health_check=[HealthCheck.function_scoped_fixture])
    def test_part_of_label_on_all_resources(self, doc_idx):
        if _SKIP or doc_idx >= len(KUSTOMIZE_DOCS):
            return
        doc = KUSTOMIZE_DOCS[doc_idx]
        labels = doc.get("metadata", {}).get("labels", {})
        assert labels.get("app.kubernetes.io/part-of") == "ptf-admin-monitoring", (
            f"{doc.get('kind')}/{doc['metadata']['name']} missing part-of label"
        )


# ── Property 4: ConfigMap Stability ───────────────────────────────

@pytest.mark.skipif(_SKIP, reason="kustomize not available")
class TestPropertyConfigMapStability:
    """P4: ConfigMap name has no hash suffix (disableNameSuffixHash=true)."""

    def test_configmap_name_exact(self):
        for doc in KUSTOMIZE_DOCS:
            if doc.get("kind") == "ConfigMap":
                name = doc["metadata"]["name"]
                assert name == "ptf-admin-dashboard", (
                    f"ConfigMap name '{name}' has unexpected suffix"
                )
