"""
S4 — Prospecting — SSRF-safe outbound fetch.

HIGH PRIORITY (owner kararı): "Public website enrichment kodu SSRF
açığına dönüşmemeli." Bu modül, app/prospecting paketi içindeki HER dış
HTTP isteğinin TEK giriş noktasıdır — enrichment.py ve discovery.py
başka hiçbir yerden doğrudan httpx çağırmaz; güvenlik kontrolünün
atlanabileceği ikinci bir yol açılmaz.

Kapsanan korumalar (owner'ın HIGH PRIORITY listesi, birebir):
- yalnız http/https şeması kabul edilir (file://, ftp://, data: vb. reddedilir)
- hostname DNS ile çözülür, DÖNEN TÜM IP'ler private/loopback/link-local/
  multicast/reserved/unspecified olup olmadığı kontrol edilir (yalnız
  hostname string'ine bakmak yetersiz — "localhost.attacker.com" gibi
  hostname'ler string eşleşmeyle yakalanamaz, gerçek çözümlenmiş IP'ye
  bakılır)
- localhost / 127.0.0.1 / ::1 / 169.254.169.254 (cloud metadata) vb.
  yukarıdaki IP kontrolünün doğal sonucu olarak engellenir
- redirect'ler MANUEL takip edilir (follow_redirects=False), HER hop'ta
  yeniden TAM SSRF kontrolü çalışır (redirect ile filtre atlatma
  engellenir), max 3 hop
- response boyutu STREAM edilerek okunur, Content-Length header'ına
  GÜVENİLMEZ (yanlış/eksik olabilir) — max_bytes aşılırsa bağlantı
  kesilir ve TOO_LARGE döner
- connect+read toplam timeout sınırlı
- yalnız text/html (+ text/plain, application/xhtml+xml) content-type
  kabul edilir — dosya indirme YOK (owner: "no auto-following file downloads")
- credential forwarding YOK — cookie/Authorization/proxy-auth hiçbir
  zaman eklenmez, her çağrı taze bir httpx.Client kullanır
- User-Agent açık ve tanımlanabilir (kim olduğumuzu gizlemiyoruz —
  sorumlu bot davranışı)

BİLİNEN RESIDUAL RİSK (owner'a açıkça bildirilir, gizlenmez — 004
migration residual'ıyla aynı dürüstlük ilkesi): DNS çözümü ile fiili
bağlantı arasındaki milisaniyelik pencerede teorik bir DNS-rebinding
saldırısı (aynı hostname'in art arda iki sorguda farklı IP döndürmesi)
TAM olarak kapatılmamıştır — bunu tam kapatmak IP-pinned bağlantı + TLS
SNI override gerektirir (production-grade bir SSRF-proxy/allowlist
altyapısı), V1 kapsamı dışında bırakıldı. Gerçekçi tehdit yüzeyi olan
"doğrudan private/localhost hedef" ve "public→private redirect zinciri"
senaryoları TAM kapatılmıştır.

Çağrıldığı yerler:
- app/prospecting/enrichment.py (website sayfa fetch'i) [S4-WB4]
- app/prospecting/discovery.py (arama sonucu sayfası fetch'i) [S4-WB4]
"""
from __future__ import annotations

import ipaddress
import logging
import socket
from dataclasses import dataclass
from typing import Optional
from urllib.parse import urljoin, urlparse

import httpx

logger = logging.getLogger(__name__)

# webhook.py'deki desenle tutarlı: taşan URL'ler loglarda kısaltılır.
_LOG_URL_MAX = 80

USER_AGENT = "GelkaProspectingBot/1.0 (+internal sales tooling; contact: gelka-portal)"

_ALLOWED_SCHEMES = {"http", "https"}
_ALLOWED_CONTENT_TYPES = ("text/html", "text/plain", "application/xhtml+xml")

DEFAULT_TIMEOUT_S = 10.0
DEFAULT_MAX_BYTES = 2_000_000  # 2 MB — HTML sayfası için fazlasıyla yeterli
DEFAULT_MAX_REDIRECTS = 3
_CHUNK_SIZE = 32_768

# fetch_status sözlüğü — ProspectSource.fetch_status ile birebir aynı
# değer kümesi (bkz. app/database.py ProspectSource docstring'i).
STATUS_OK = "OK"
STATUS_FAILED = "FAILED"
STATUS_BLOCKED_SSRF = "BLOCKED_SSRF"
STATUS_TIMEOUT = "TIMEOUT"
STATUS_TOO_LARGE = "TOO_LARGE"
STATUS_UNSUPPORTED_CONTENT_TYPE = "UNSUPPORTED_CONTENT_TYPE"


@dataclass
class FetchResult:
    status: str
    final_url: Optional[str] = None
    status_code: Optional[int] = None
    content_type: Optional[str] = None
    text: Optional[str] = None
    error_message: Optional[str] = None  # kullanıcıya güvenle gösterilebilir, kısa, PII içermez

    @property
    def ok(self) -> bool:
        return self.status == STATUS_OK


def _truncate_for_log(url: str) -> str:
    return url if len(url) <= _LOG_URL_MAX else url[:_LOG_URL_MAX] + "…"


def _is_blocked_ip(ip: "ipaddress._BaseAddress") -> bool:
    """
    Private/loopback/link-local/multicast/reserved/unspecified TÜM
    aralıkları reddet. is_global (Python 3.13'te mevcut) yerine tek tek
    bayraklara bakılır — eski/yeni Python sürümleri arasında tutarlı
    davranış için (backend/.venv Python 3.13 olsa da packaged exe farklı
    bir yorumlayıcıyla derlenmiş olabilir, bkz. backend-runtime-env belleği).
    """
    return (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


class SSRFBlockedError(Exception):
    """GERÇEK bir güvenlik reddi (private/loopback/link-local/vb. IP) — çağıran BLOCKED_SSRF olarak işler."""


class HostResolutionError(Exception):
    """
    DNS çözümlenemedi (domain yok/kayıtlı değil/yazım hatası). Bu bir
    SSRF TEHDİDİ DEĞİLDİR — SSRFBlockedError'dan BİLİNÇLİ OLARAK ayrı
    tutulur, aksi halde "var olmayan bir domain" ile "bilerek private bir
    IP'ye çözümlenen bir domain" aynı (yanıltıcı) BLOCKED_SSRF sonucuna
    düşerdi. Çağıran bunu STATUS_FAILED olarak işler (gerçek bulgu: ilk
    yazımda bu ayrım yoktu, canlı testte fark edilip düzeltildi).
    """


def _validate_host_or_raise(hostname: str) -> None:
    if not hostname:
        raise SSRFBlockedError("boş hostname")

    # IPv6 literal'ler urlparse'ta köşeli parantezli gelir ([::1]) — soy.
    bare = hostname.strip("[]")

    # Önce doğrudan IP literal mi diye bak (DNS'e gitmeden karar verilebilir).
    try:
        ip_literal = ipaddress.ip_address(bare)
        if _is_blocked_ip(ip_literal):
            raise SSRFBlockedError(f"engellenmiş IP: {bare}")
        return
    except ValueError:
        pass  # IP literal değil, hostname — DNS çözümü gerekli

    # DNS çözümü: TÜM dönen adresler kontrol edilir (fail-closed — biri
    # bile engellenmiş aralıktaysa tüm hostname reddedilir; DNS
    # round-robin/rebinding senaryosunda güvenli tarafta kalınır).
    try:
        addrinfo = socket.getaddrinfo(bare, None)
    except socket.gaierror as exc:
        raise HostResolutionError(f"DNS çözümlenemedi: {exc}") from exc

    if not addrinfo:
        raise HostResolutionError("DNS çözümü boş sonuç döndü")

    for family, _, _, _, sockaddr in addrinfo:
        raw_ip = sockaddr[0]
        try:
            ip_obj = ipaddress.ip_address(raw_ip)
        except ValueError:
            raise HostResolutionError(f"geçersiz çözümlenmiş IP: {raw_ip}")
        if _is_blocked_ip(ip_obj):
            raise SSRFBlockedError(f"hostname özel/yerel bir IP'ye çözümleniyor: {bare} -> {raw_ip}")


def _validate_url_or_raise(url: str) -> str:
    """Şema + hostname kontrolü. Geçerliyse normalize edilmiş hostname döner."""
    parsed = urlparse(url)
    if parsed.scheme not in _ALLOWED_SCHEMES:
        raise SSRFBlockedError(f"desteklenmeyen şema: {parsed.scheme!r}")
    if not parsed.hostname:
        raise SSRFBlockedError("URL'de hostname yok")
    _validate_host_or_raise(parsed.hostname)
    return parsed.hostname


def safe_get(
    url: str,
    *,
    timeout_s: float = DEFAULT_TIMEOUT_S,
    max_bytes: int = DEFAULT_MAX_BYTES,
    max_redirects: int = DEFAULT_MAX_REDIRECTS,
) -> FetchResult:
    """
    SSRF-korumalı, boyut/timeout/content-type sınırlı senkron GET.

    "Hiçbir kayıt sessizce kaybolmayacak" ilkesi (S3'ten taşınan genel
    ilke, S4'te de geçerli): bu fonksiyon HİÇBİR durumda exception
    fırlatmaz — her sonuç (başarı, SSRF reddi, timeout, boyut aşımı,
    desteklenmeyen content-type, genel hata) bir FetchResult olarak
    döner; çağıran (enrichment.py) bunu doğrudan bir ProspectSource
    satırına çevirir (fetch_status alanı bu STATUS_* sabitleriyle
    birebir eşleşir).
    """
    current_url = url
    headers = {
        "User-Agent": USER_AGENT,
        "Accept": "text/html,application/xhtml+xml,text/plain;q=0.8,*/*;q=0.1",
    }

    for hop in range(max_redirects + 1):
        try:
            _validate_url_or_raise(current_url)
        except SSRFBlockedError as exc:
            logger.info("SSRF reddi (%s): %s", _truncate_for_log(current_url), exc)
            return FetchResult(status=STATUS_BLOCKED_SSRF, final_url=current_url, error_message=str(exc))
        except HostResolutionError as exc:
            # Güvenlik reddi DEĞİL — yalnız kaynağa ulaşılamadı (yanlış
            # domain/DNS hatası). BLOCKED_SSRF ile KARIŞTIRILMAZ.
            logger.info("DNS çözümlenemedi (%s): %s", _truncate_for_log(current_url), exc)
            return FetchResult(status=STATUS_FAILED, final_url=current_url, error_message="kaynağa ulaşılamadı (DNS)")

        try:
            # Credential forwarding YOK: taze client, cookie/auth store yok,
            # follow_redirects=False (redirect'i BİZ manuel doğrularız).
            with httpx.Client(
                follow_redirects=False,
                timeout=httpx.Timeout(timeout_s),
                headers=headers,
            ) as client:
                with client.stream("GET", current_url) as response:
                    if response.is_redirect:
                        location = response.headers.get("location")
                        response.close()
                        if not location:
                            return FetchResult(
                                status=STATUS_FAILED, final_url=current_url,
                                status_code=response.status_code,
                                error_message="redirect ama Location header yok",
                            )
                        current_url = urljoin(current_url, location)
                        continue  # bir sonraki hop'ta yeniden SSRF doğrulanır

                    content_type_header = response.headers.get("content-type", "")
                    content_type = content_type_header.split(";")[0].strip().lower()
                    if content_type and not any(content_type.startswith(ct) for ct in _ALLOWED_CONTENT_TYPES):
                        response.close()
                        return FetchResult(
                            status=STATUS_UNSUPPORTED_CONTENT_TYPE,
                            final_url=str(response.url),
                            status_code=response.status_code,
                            content_type=content_type,
                            error_message=f"desteklenmeyen content-type: {content_type}",
                        )

                    if response.status_code >= 400:
                        response.close()
                        return FetchResult(
                            status=STATUS_FAILED,
                            final_url=str(response.url),
                            status_code=response.status_code,
                            error_message=f"HTTP {response.status_code}",
                        )

                    # Content-Length'e GÜVENİLMEZ — stream ederek gerçek
                    # boyutu biz sayarız, aşılırsa hemen keseriz.
                    body = bytearray()
                    too_large = False
                    for chunk in response.iter_bytes(chunk_size=_CHUNK_SIZE):
                        body.extend(chunk)
                        if len(body) > max_bytes:
                            too_large = True
                            break
                    if too_large:
                        response.close()
                        return FetchResult(
                            status=STATUS_TOO_LARGE,
                            final_url=str(response.url),
                            status_code=response.status_code,
                            content_type=content_type,
                            error_message=f"yanıt {max_bytes} byte sınırını aştı",
                        )

                    encoding = response.encoding or "utf-8"
                    try:
                        text = bytes(body).decode(encoding, errors="replace")
                    except (LookupError, TypeError):
                        text = bytes(body).decode("utf-8", errors="replace")

                    return FetchResult(
                        status=STATUS_OK,
                        final_url=str(response.url),
                        status_code=response.status_code,
                        content_type=content_type,
                        text=text,
                    )
        except httpx.TimeoutException:
            logger.info("Timeout: %s", _truncate_for_log(current_url))
            return FetchResult(status=STATUS_TIMEOUT, final_url=current_url, error_message="istek zaman aşımına uğradı")
        except httpx.RequestError as exc:
            logger.info("İstek hatası (%s): %s", _truncate_for_log(current_url), type(exc).__name__)
            return FetchResult(status=STATUS_FAILED, final_url=current_url, error_message=f"istek hatası: {type(exc).__name__}")

    return FetchResult(status=STATUS_FAILED, final_url=current_url, error_message="çok fazla redirect")
