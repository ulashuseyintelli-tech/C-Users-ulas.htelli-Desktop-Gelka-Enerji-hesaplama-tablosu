#!/usr/bin/env python3
"""
Post-Deploy Validation Script - Sprint 8.9.1

CI/CD veya manuel çalıştırılabilir doğrulama scripti.

EXIT CODES:
  0 = All checks passed
  1 = Ready check failed (ROLLBACK)
  2 = Smoke test failed (ROLLBACK)
  3 = Feedback loop failed (INVESTIGATE)
  4 = Partial success (INVESTIGATE)

USAGE:
  # Environment variables
  export API_BASE_URL="https://api.example.com"
  export API_KEY="your-api-key"
  export ADMIN_API_KEY="your-admin-key"
  
  # Run
  python scripts/post_deploy_check.py
  
  # Check exit code
  echo $?

ENVIRONMENT VARIABLES:
  API_BASE_URL: Base URL of the API (default: http://localhost:8000)
  API_KEY: API key for authenticated endpoints
  ADMIN_API_KEY: Admin API key for admin endpoints
  PILOT_TENANT_ID: Tenant ID for pilot checks (default: pilot)
  CHECK_TIMEOUT: Request timeout in seconds (default: 30)
  VERBOSE: Set to "true" for detailed output
"""

import os
import sys
import json
import time
from datetime import datetime, timezone
from typing import Optional, Tuple

# Try to import requests, fall back to urllib if not available
try:
    import requests
    HAS_REQUESTS = True
except ImportError:
    import urllib.request
    import urllib.error
    HAS_REQUESTS = False


# ═══════════════════════════════════════════════════════════════════════════════
# CONFIGURATION
# ═══════════════════════════════════════════════════════════════════════════════

BASE_URL = os.getenv("API_BASE_URL", "http://localhost:8000")
API_KEY = os.getenv("API_KEY", "")
ADMIN_API_KEY = os.getenv("ADMIN_API_KEY", "")
TENANT_ID = os.getenv("PILOT_TENANT_ID", "pilot")
TIMEOUT = int(os.getenv("CHECK_TIMEOUT", "30"))
VERBOSE = os.getenv("VERBOSE", "false").lower() == "true"


# ═══════════════════════════════════════════════════════════════════════════════
# HTTP HELPERS
# ═══════════════════════════════════════════════════════════════════════════════

def http_get(url: str, headers: dict = None, timeout: int = TIMEOUT) -> Tuple[int, dict]:
    """
    Make HTTP GET request.
    
    Returns:
        (status_code, response_json or error_dict)
    """
    headers = headers or {}
    
    if HAS_REQUESTS:
        try:
            resp = requests.get(url, headers=headers, timeout=timeout)
            try:
                data = resp.json()
            except:
                data = {"raw": resp.text[:500]}
            return resp.status_code, data
        except requests.exceptions.Timeout:
            return 0, {"error": "timeout"}
        except requests.exceptions.ConnectionError as e:
            return 0, {"error": f"connection_error: {str(e)[:100]}"}
        except Exception as e:
            return 0, {"error": str(e)[:100]}
    else:
        # Fallback to urllib
        try:
            req = urllib.request.Request(url, headers=headers)
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                data = json.loads(resp.read().decode())
                return resp.status, data
        except urllib.error.HTTPError as e:
            try:
                data = json.loads(e.read().decode())
            except:
                data = {"error": str(e)}
            return e.code, data
        except Exception as e:
            return 0, {"error": str(e)[:100]}


def http_post(url: str, data: dict = None, headers: dict = None, timeout: int = TIMEOUT) -> Tuple[int, dict]:
    """
    Make HTTP POST request.
    
    Returns:
        (status_code, response_json or error_dict)
    """
    headers = headers or {}
    headers["Content-Type"] = "application/json"
    
    if HAS_REQUESTS:
        try:
            resp = requests.post(url, json=data, headers=headers, timeout=timeout)
            try:
                result = resp.json()
            except:
                result = {"raw": resp.text[:500]}
            return resp.status_code, result
        except requests.exceptions.Timeout:
            return 0, {"error": "timeout"}
        except requests.exceptions.ConnectionError as e:
            return 0, {"error": f"connection_error: {str(e)[:100]}"}
        except Exception as e:
            return 0, {"error": str(e)[:100]}
    else:
        # Fallback to urllib
        try:
            body = json.dumps(data or {}).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                result = json.loads(resp.read().decode())
                return resp.status, result
        except urllib.error.HTTPError as e:
            try:
                result = json.loads(e.read().decode())
            except:
                result = {"error": str(e)}
            return e.code, result
        except Exception as e:
            return 0, {"error": str(e)[:100]}


# ═══════════════════════════════════════════════════════════════════════════════
# CHECK FUNCTIONS
# ═══════════════════════════════════════════════════════════════════════════════

def check_ready() -> Tuple[bool, str]:
    """
    Step 1: GET /health/ready → 200
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/health/ready"
    status, data = http_get(url)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    if status != 200:
        return False, f"Status {status}: {data}"
    
    if data.get("status") != "ready":
        checks = data.get("checks", {})
        failing = data.get("failing_checks", [])
        return False, f"Not ready: status={data.get('status')}, failing={failing}, checks={checks}"
    
    build_id = data.get("build_id", "unknown")
    config_hash = data.get("config_hash", "unknown")
    
    return True, f"Ready: build={build_id}, config={config_hash}"


def check_health_basic() -> Tuple[bool, str]:
    """
    Step 1b: GET /health → 200 (basic alive check)
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/health"
    status, data = http_get(url)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    if status != 200:
        return False, f"Status {status}: {data}"
    
    return True, f"Alive: {data}"


def check_config_validation() -> Tuple[bool, str]:
    """
    Step 2: Verify config validation is active via /health/ready checks.
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/health/ready"
    status, data = http_get(url)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    checks = data.get("checks", {})
    config_check = checks.get("config", {})
    
    if config_check.get("status") != "ok":
        return False, f"Config check failed: {config_check}"
    
    if not config_check.get("validated"):
        return False, "Config not validated"
    
    return True, "Config validation active"


def check_database() -> Tuple[bool, str]:
    """
    Step 3: Verify database connection via /health/ready checks.
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/health/ready"
    status, data = http_get(url)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    checks = data.get("checks", {})
    db_check = checks.get("database", {})
    
    if db_check.get("status") == "error":
        return False, f"Database error: {db_check}"
    
    latency = db_check.get("latency_ms", -1)
    if latency > 500:
        return False, f"Database latency too high: {latency}ms"
    
    return True, f"Database OK: latency={latency}ms"


def check_system_health() -> Tuple[bool, str]:
    """
    Step 4: GET /admin/system-health (if available)
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/admin/system-health"
    headers = {}
    if ADMIN_API_KEY:
        headers["X-Admin-Key"] = ADMIN_API_KEY
    
    params = f"?tenant_id={TENANT_ID}" if TENANT_ID else ""
    status, data = http_get(f"{url}{params}", headers=headers)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    if status == 404:
        return True, "System health endpoint not found (OK for initial deploy)"
    
    if status == 401 or status == 403:
        return True, "System health requires auth (OK if ADMIN_API_KEY not set)"
    
    if status != 200:
        return False, f"Status {status}: {data}"
    
    return True, f"System health accessible"


def check_feedback_endpoint() -> Tuple[bool, str]:
    """
    Step 5: Check feedback endpoint exists (GET /admin/feedback-stats)
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/admin/feedback-stats"
    headers = {}
    if ADMIN_API_KEY:
        headers["X-Admin-Key"] = ADMIN_API_KEY
    
    params = f"?tenant_id={TENANT_ID}" if TENANT_ID else ""
    status, data = http_get(f"{url}{params}", headers=headers)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    if status == 404:
        return True, "Feedback stats endpoint not found (OK for initial deploy)"
    
    if status == 401 or status == 403:
        return True, "Feedback stats requires auth (OK if ADMIN_API_KEY not set)"
    
    if status != 200:
        # Not a hard failure for feedback
        return True, f"Feedback stats returned {status} (investigate but don't rollback)"
    
    coverage = data.get("feedback_coverage", 0)
    return True, f"Feedback stats OK: coverage={coverage}"


def check_queue_status() -> Tuple[bool, str]:
    """
    Step 6: Check queue status via /health/ready
    
    Returns:
        (success, message)
    """
    url = f"{BASE_URL}/health/ready"
    status, data = http_get(url)
    
    if status == 0:
        return False, f"Connection failed: {data.get('error', 'unknown')}"
    
    checks = data.get("checks", {})
    queue_check = checks.get("queue", {})
    
    depth = queue_check.get("depth", 0)
    stuck = queue_check.get("stuck_count", 0)
    
    if stuck > 0:
        return False, f"Queue has {stuck} stuck job(s)"
    
    if depth > 100:
        return False, f"Queue backlog too high: {depth}"
    
    return True, f"Queue OK: depth={depth}, stuck={stuck}"


# ═══════════════════════════════════════════════════════════════════════════════
# MAIN
# ═══════════════════════════════════════════════════════════════════════════════

def print_result(name: str, success: bool, message: str) -> None:
    """Print check result."""
    icon = "✅" if success else "❌"
    print(f"{icon} {name}: {message}")


def main() -> int:
    """
    Run all post-deploy checks.
    
    Returns:
        Exit code (0-4)
    """
    print(f"\n{'='*60}")
    print(f"POST-DEPLOY VALIDATION")
    print(f"{'='*60}")
    print(f"Target: {BASE_URL}")
    print(f"Tenant: {TENANT_ID}")
    print(f"Time: {datetime.now(timezone.utc).isoformat()}")
    print(f"{'='*60}\n")
    
    results = {}
    
    # Critical checks (failure = rollback)
    print("CRITICAL CHECKS (failure = rollback)")
    print("-" * 40)
    
    # 1. Basic health
    success, msg = check_health_basic()
    results["health_basic"] = success
    print_result("Health (basic)", success, msg)
    
    if not success:
        print(f"\n❌ CRITICAL: Basic health check failed")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 1
    
    # 2. Ready check
    success, msg = check_ready()
    results["ready"] = success
    print_result("Health (ready)", success, msg)
    
    if not success:
        print(f"\n❌ CRITICAL: Ready check failed")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 1
    
    # 3. Config validation
    success, msg = check_config_validation()
    results["config"] = success
    print_result("Config validation", success, msg)
    
    if not success:
        print(f"\n❌ CRITICAL: Config validation failed")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 1
    
    # 4. Database
    success, msg = check_database()
    results["database"] = success
    print_result("Database", success, msg)
    
    if not success:
        print(f"\n❌ CRITICAL: Database check failed")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 2
    
    # 5. Queue
    success, msg = check_queue_status()
    results["queue"] = success
    print_result("Queue status", success, msg)
    
    if not success:
        print(f"\n❌ CRITICAL: Queue check failed")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 2
    
    # Non-critical checks (failure = investigate)
    print("\nNON-CRITICAL CHECKS (failure = investigate)")
    print("-" * 40)
    
    # 6. System health
    success, msg = check_system_health()
    results["system_health"] = success
    print_result("System health", success, msg)
    
    # 7. Feedback
    success, msg = check_feedback_endpoint()
    results["feedback"] = success
    print_result("Feedback endpoint", success, msg)
    
    # Summary
    print(f"\n{'='*60}")
    print("SUMMARY")
    print(f"{'='*60}")
    
    critical_passed = all([
        results.get("health_basic"),
        results.get("ready"),
        results.get("config"),
        results.get("database"),
        results.get("queue"),
    ])
    
    all_passed = all(results.values())
    
    if all_passed:
        print("\n✅ ALL CHECKS PASSED")
        print("ACTION: Deploy successful, proceed with pilot")
        return 0
    elif critical_passed:
        print("\n⚠️ PARTIAL SUCCESS")
        print("Critical checks passed, some non-critical checks failed")
        print("ACTION: Investigate non-critical failures, don't rollback")
        return 4
    else:
        print("\n❌ CRITICAL FAILURE")
        print("ACTION: ROLLBACK IMMEDIATELY")
        return 1


if __name__ == "__main__":
    try:
        exit_code = main()
        sys.exit(exit_code)
    except KeyboardInterrupt:
        print("\n\nAborted by user")
        sys.exit(130)
    except Exception as e:
        print(f"\n❌ UNEXPECTED ERROR: {e}")
        import traceback
        traceback.print_exc()
        sys.exit(1)
