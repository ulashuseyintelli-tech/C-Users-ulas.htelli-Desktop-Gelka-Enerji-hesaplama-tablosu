"""
Failure Taxonomy — merkezi hata sınıflandırması (DW-4).

Tüm exception→CB failure sınıflandırması bu dosyada.
Wrapper'lar kendi sınıflandırması yapmaz; her zaman bu modülü çağırır.

Kurallar:
- CB failure: TimeoutError, ConnectionError, ConnectionRefusedError, OSError, HTTP 5xx
- CB non-failure: HTTP 429, HTTP 4xx (429 hariç), ValueError, ValidationError

Feature: dependency-wrappers, Task 3
"""

# CB failure olarak sayılan exception türleri
CB_FAILURE_EXCEPTIONS: tuple[type[Exception], ...] = (
    TimeoutError,
    ConnectionError,
    ConnectionRefusedError,
    OSError,
)


def is_cb_failure(exc: Exception) -> bool:
    """
    Exception'ın CB failure olarak sayılıp sayılmayacağını belirle.

    True: TimeoutError, ConnectionError, ConnectionRefusedError, OSError, HTTP 5xx
    False: HTTP 4xx (429 dahil), ValueError, ValidationError, diğer
    """
    if isinstance(exc, CB_FAILURE_EXCEPTIONS):
        return True

    # HTTP yanıt hatası kontrolü (httpx veya benzeri client)
    if hasattr(exc, "response") and hasattr(exc.response, "status_code"):
        return exc.response.status_code >= 500

    return False


def is_cb_failure_status(status_code: int) -> bool:
    """HTTP status code'a göre CB failure kontrolü. 5xx → True, diğer → False."""
    return status_code >= 500


def is_retryable(exc: Exception) -> bool:
    """
    Exception'ın retry edilebilir olup olmadığını belirle.

    Retry edilebilir = CB failure olan hatalar (timeout, connection, 5xx).
    Non-CB failure (4xx, ValueError vb.) retry edilmez.
    İdempotent kontrolü wrapper'a bırakılır (DW-1).
    """
    return is_cb_failure(exc)
