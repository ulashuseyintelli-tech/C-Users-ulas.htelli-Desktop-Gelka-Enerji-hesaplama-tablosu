"""
Dependency Wrapper — dış bağımlılık çağrılarını saran base sınıf.

Sorumluluklar:
- CB allow_request kontrolü
- Timeout uygulaması (asyncio.wait_for)
- Retry politikası (exponential backoff + jitter, DW-1)
- CB record_success / record_failure
- Failure taxonomy (DW-4: tek kaynak)
- Metrik kaydı
- Wrapper iç hatası → fail-open + metric (DW-3)

CB scope = process-local. Her worker kendi registry'sini tutar.

Feature: dependency-wrappers, Task 8
"""

import asyncio
import logging
import random
import time
from typing import Awaitable, Callable, TypeVar

from .circuit_breaker import CircuitBreaker, CircuitBreakerRegistry, Dependency
from .failure_taxonomy import is_cb_failure
from ..guard_config import GuardConfig
from ..ptf_metrics import PTFMetrics

logger = logging.getLogger(__name__)
T = TypeVar("T")


class CircuitOpenError(Exception):
    """CB OPEN durumunda fırlatılır — beklenen hata, fail-open uygulanmaz."""

    def __init__(self, dependency: str):
        self.dependency = dependency
        super().__init__(f"Circuit breaker open for {dependency}")


class DependencyWrapper:
    """
    Dış bağımlılık çağrılarını saran base sınıf.

    Akış: CB allow? → timeout → call → success/failure → retry?
    DW-1: is_write=True → retry kapalı (double-write riski)
    DW-3: Wrapper iç hatası → fail-open + metric
    DW-4: Failure taxonomy is_cb_failure() ile merkezileştirilmiş
    """

    def __init__(
        self,
        dependency: Dependency,
        cb: CircuitBreaker,
        config: GuardConfig,
        metrics: PTFMetrics,
    ) -> None:
        self._dependency = dependency
        self._cb = cb
        self._config = config
        self._metrics = metrics

    @property
    def dependency_name(self) -> str:
        return self._dependency.value

    async def call(
        self,
        fn: Callable[..., Awaitable[T]],
        *args,
        is_write: bool = False,
        **kwargs,
    ) -> T:
        """
        Bağımlılık çağrısını sar: CB check → timeout → retry → metrik.

        Raises:
            CircuitOpenError: CB OPEN durumunda
            asyncio.TimeoutError: Tüm retry'lar tükendikten sonra timeout
            Exception: Dependency hatası (CB failure veya non-CB failure)
        """
        try:
            return await self._call_inner(fn, *args, is_write=is_write, **kwargs)
        except (CircuitOpenError, asyncio.TimeoutError):
            raise  # Beklenen hatalar — fail-open uygulanmaz
        except Exception as exc:
            if is_cb_failure(exc):
                raise  # Dependency hatası — yukarı fırlat
            # Bilinmeyen hata — dependency hatası mı wrapper bug mı?
            # Non-CB failure → direkt fırlat (4xx, ValueError vb.)
            raise

    async def _call_inner(
        self,
        fn: Callable[..., Awaitable[T]],
        *args,
        is_write: bool = False,
        **kwargs,
    ) -> T:
        """İç çağrı döngüsü: CB check → timeout → retry."""
        # DW-1: Write path'te retry kapalı (double-write riski)
        can_retry = not is_write or self._config.wrapper_retry_on_write
        max_retries = (
            self._config.get_retry_max_attempts_for_dependency(self.dependency_name)
            if can_retry
            else 0
        )
        timeout = self._config.get_timeout_for_dependency(self.dependency_name)
        base_ms = self._config.wrapper_retry_backoff_base_ms
        cap_ms = self._config.wrapper_retry_backoff_cap_ms
        jitter_pct = self._config.wrapper_retry_jitter_pct
        last_exc: Exception | None = None

        for attempt in range(1 + max_retries):
            # CB pre-check (call-site level)
            if not self._cb.allow_request():
                self._metrics.inc_dependency_call(self.dependency_name, "circuit_open")
                raise CircuitOpenError(self.dependency_name)

            start = time.monotonic()
            try:
                result = await asyncio.wait_for(
                    fn(*args, **kwargs), timeout=timeout
                )
                duration = time.monotonic() - start
                self._cb.record_success()
                self._metrics.inc_dependency_call(self.dependency_name, "success")
                self._metrics.observe_dependency_call_duration(
                    self.dependency_name, duration
                )
                return result

            except asyncio.TimeoutError:
                duration = time.monotonic() - start
                self._metrics.observe_dependency_call_duration(
                    self.dependency_name, duration
                )
                self._cb.record_failure()
                self._metrics.inc_dependency_call(self.dependency_name, "timeout")
                last_exc = asyncio.TimeoutError(
                    f"{self.dependency_name} timeout after {timeout}s"
                )

            except Exception as exc:
                duration = time.monotonic() - start
                self._metrics.observe_dependency_call_duration(
                    self.dependency_name, duration
                )

                if is_cb_failure(exc):
                    self._cb.record_failure()
                    self._metrics.inc_dependency_call(self.dependency_name, "failure")
                    last_exc = exc
                else:
                    # Non-CB failure → retry yapma, direkt fırlat
                    # CB'ye failure saydırma ama observability'de görünür tut
                    self._metrics.inc_dependency_call(self.dependency_name, "client_error")
                    raise

            # Retry kontrolü
            if attempt < max_retries:
                # CB hala açık mı kontrol et
                if not self._cb.allow_request():
                    self._metrics.inc_dependency_call(
                        self.dependency_name, "circuit_open"
                    )
                    raise CircuitOpenError(self.dependency_name)

                # Exponential backoff + jitter
                delay_ms = min(base_ms * (2**attempt), cap_ms)
                jitter = random.uniform(0, delay_ms * jitter_pct)
                delay_s = (delay_ms + jitter) / 1000.0
                self._metrics.inc_dependency_retry(self.dependency_name)
                logger.warning(
                    f"[DEP-WRAPPER] {self.dependency_name} retry "
                    f"{attempt + 1}/{max_retries}, delay={delay_s:.3f}s"
                )
                await asyncio.sleep(delay_s)

        # Tüm retry'lar tükendi
        raise last_exc  # type: ignore[misc]


# ── Concrete Wrapper Sınıfları ────────────────────────────────────────────────


class DBClientWrapper(DependencyWrapper):
    """DB Primary/Replica çağrıları için wrapper."""
    pass


class ExternalAPIClientWrapper(DependencyWrapper):
    """External API (OpenAI vb.) çağrıları için wrapper."""
    pass


class CacheClientWrapper(DependencyWrapper):
    """Cache çağrıları için wrapper."""
    pass


# ── Factory ───────────────────────────────────────────────────────────────────

_WRAPPER_CLASSES: dict[Dependency, type[DependencyWrapper]] = {
    Dependency.DB_PRIMARY: DBClientWrapper,
    Dependency.DB_REPLICA: DBClientWrapper,
    Dependency.CACHE: CacheClientWrapper,
    Dependency.EXTERNAL_API: ExternalAPIClientWrapper,
    Dependency.IMPORT_WORKER: DBClientWrapper,
}


def create_wrapper(
    dependency: Dependency,
    cb_registry: CircuitBreakerRegistry,
    config: GuardConfig,
    metrics: PTFMetrics,
) -> DependencyWrapper:
    """Bağımlılık türüne göre uygun wrapper oluştur."""
    cls = _WRAPPER_CLASSES.get(dependency, DependencyWrapper)
    cb = cb_registry.get(dependency.value)
    return cls(dependency, cb, config, metrics)
