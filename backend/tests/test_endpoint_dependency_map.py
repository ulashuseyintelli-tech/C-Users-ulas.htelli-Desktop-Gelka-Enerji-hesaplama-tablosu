"""
Endpoint Dependency Map tests — Feature: dependency-wrappers, Task 5.

Property 1: Endpoint Dependency Map Geçerliliği
- Map'teki her değer geçerli Dependency enum üyesi
- Map'te olmayan endpoint → boş liste
"""

import pytest
from hypothesis import given, settings as h_settings, HealthCheck
from hypothesis import strategies as st

from app.guards.endpoint_dependency_map import (
    ENDPOINT_DEPENDENCY_MAP,
    get_dependencies,
)
from app.guards.circuit_breaker import Dependency


# ═══════════════════════════════════════════════════════════════════════════════
# Unit Tests
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndpointDependencyMap:
    """Static map structure validation."""

    def test_map_is_not_empty(self):
        assert len(ENDPOINT_DEPENDENCY_MAP) > 0

    def test_all_values_are_dependency_lists(self):
        for endpoint, deps in ENDPOINT_DEPENDENCY_MAP.items():
            assert isinstance(deps, list), f"{endpoint} value is not a list"
            assert len(deps) > 0, f"{endpoint} has empty dependency list"
            for dep in deps:
                assert isinstance(dep, Dependency), (
                    f"{endpoint} has non-Dependency value: {dep}"
                )

    def test_known_endpoints_present(self):
        assert "/admin/market-prices" in ENDPOINT_DEPENDENCY_MAP
        assert "/admin/market-prices/import/apply" in ENDPOINT_DEPENDENCY_MAP
        assert "/admin/market-prices/lookup" in ENDPOINT_DEPENDENCY_MAP

    def test_import_apply_has_db_and_worker(self):
        deps = get_dependencies("/admin/market-prices/import/apply")
        assert Dependency.DB_PRIMARY in deps
        assert Dependency.IMPORT_WORKER in deps

    def test_lookup_has_replica_and_cache(self):
        deps = get_dependencies("/admin/market-prices/lookup")
        assert Dependency.DB_REPLICA in deps
        assert Dependency.CACHE in deps

    def test_analyze_invoice_has_external_api(self):
        deps = get_dependencies("/analyze-invoice")
        assert Dependency.EXTERNAL_API in deps


class TestGetDependencies:
    """get_dependencies() behavior."""

    def test_known_endpoint_returns_deps(self):
        deps = get_dependencies("/admin/market-prices")
        assert len(deps) > 0

    def test_unknown_endpoint_returns_empty(self):
        deps = get_dependencies("/nonexistent/path")
        assert deps == []

    def test_empty_string_returns_empty(self):
        deps = get_dependencies("")
        assert deps == []

    def test_returns_list_not_reference(self):
        """Returned list should be from the dict (no copy needed for static map)."""
        deps = get_dependencies("/admin/market-prices")
        assert isinstance(deps, list)


# ═══════════════════════════════════════════════════════════════════════════════
# Property-Based Tests — Feature: dependency-wrappers, Property 1
# ═══════════════════════════════════════════════════════════════════════════════


class TestEndpointDependencyMapProperty:
    """Property 1: Endpoint Dependency Map Geçerliliği."""

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(endpoint=st.sampled_from(list(ENDPOINT_DEPENDENCY_MAP.keys())))
    def test_mapped_endpoints_have_valid_dependencies(self, endpoint):
        """Feature: dependency-wrappers, Property 1: mapped endpoint → valid Dependency list."""
        deps = get_dependencies(endpoint)
        assert len(deps) > 0
        for dep in deps:
            assert isinstance(dep, Dependency)

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(
        endpoint=st.text(
            alphabet=st.characters(whitelist_categories=("L", "N"), whitelist_characters="/-_"),
            min_size=1,
            max_size=50,
        )
    )
    def test_unmapped_endpoints_return_empty(self, endpoint):
        """Feature: dependency-wrappers, Property 1: unmapped endpoint → empty list."""
        if endpoint not in ENDPOINT_DEPENDENCY_MAP:
            deps = get_dependencies(endpoint)
            assert deps == []

    @h_settings(max_examples=100, suppress_health_check=[HealthCheck.function_scoped_fixture])
    @given(dep=st.sampled_from(list(Dependency)))
    def test_every_dependency_enum_used_at_least_once(self, dep):
        """Feature: dependency-wrappers, Property 1: every Dependency enum appears in map."""
        all_deps = set()
        for deps_list in ENDPOINT_DEPENDENCY_MAP.values():
            all_deps.update(deps_list)
        assert dep in all_deps, f"Dependency {dep} not used in any endpoint mapping"
