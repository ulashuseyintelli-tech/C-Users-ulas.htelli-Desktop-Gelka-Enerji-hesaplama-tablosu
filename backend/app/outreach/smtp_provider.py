"""
S5 — Outreach — smtp_provider.py

OutboundMailProvider soyutlaması + tek V1 implementasyonu: authenticated
SMTP (owner'ın SMTP kararı — Microsoft Graph/Gmail API/OAuth adapter YOK,
yalnız mevcut hosting posta kutusu: info@gelkaenerji.com.tr @
srvc73.trwww.com). Transport parametreleri (host/port/security/username/
password) app/core/config.py'den okunur, HİÇBİR YERDE hard-code EDİLMEZ
(owner: "Do not hard-code transport parameters").

Kimlik bilgisi disiplini (owner kararı, S5 SMTP mesajı):
- username = tam posta kutusu adresi
- parola machine-local .env'de kalır; asla git/installer/frontend/LOG'a girmez
- Bu modül parolayı LOGLAMAZ (hata mesajlarında bile) — yalnız smtplib'e geçirir

Güvenlik (owner'ın WB8 test kategorileri, burada TASARIM zamanında
uygulanıyor — sonradan "test edip düzeltmek" değil):
- Header injection: subject/to_email/reply_to içinde CR/LF varsa REDDEDİLİR
  (klasik e-posta header injection saldırısı — Bcc enjeksiyonu vb.).
- V1 yalnız text/plain MIME gönderir (EmailMessage.set_content, HTML gövde
  YOK) — HTML/XSS enjeksiyon sınıfı mimari olarak devre dışı.
- Bağlantı/okuma timeout'u sabit ve kısa — sonsuz asılı kalma YOK (owner:
  "provider-timeout" test kategorisi).
- security alanı yalnız "starttls"/"implicit_tls" kabul eder — geçersiz bir
  .env değeri SESSİZCE bir moda düşürülmez, CONFIG_INVALID ile reddedilir
  ("sessiz inference yapma" ilkesinin transport katmanına uzantısı).

İdempotency/atomic SENDING claim BURADA DEĞİL — bu modül "verilen mesajı
göndermeyi DENE, sonucu döndür" işini yapar; hangi mesajın gönderilmeye
UYGUN olduğu (DB durumu, compare-and-swap) app/outreach/service.py'nin
sorumluluğudur [S5-WB5, service.py].

Çağrıldığı yerler:
- (henüz yok) app/outreach/service.py send akışı [S5-WB5]
"""
from __future__ import annotations

import logging
import smtplib
import ssl
from abc import ABC, abstractmethod
from dataclasses import dataclass
from email.message import EmailMessage
from email.utils import make_msgid
from typing import Optional

from ..core.config import settings

logger = logging.getLogger(__name__)

_CONNECT_TIMEOUT_SECONDS = 20

# E-posta header injection'ı önlemek için: subject/adres alanlarında CR/LF
# ASLA kabul edilmez (RFC 5322 header'ları newline ile ayrılır — bir
# saldırgan subject'e "\r\nBcc: hedef@..." enjekte edebilirdi).
_FORBIDDEN_HEADER_CHARS = ("\r", "\n")


class OutboundMailProviderError(Exception):
    """Taban hata sınıfı — error_code ile birlikte (send() öncesi doğrulama hataları için)."""

    def __init__(self, error_code: str, detail: str):
        self.error_code = error_code
        self.detail = detail
        super().__init__(f"{error_code}: {detail}")


@dataclass
class SendResult:
    success: bool
    provider_message_id: Optional[str] = None
    error_code: Optional[str] = None
    error_detail: Optional[str] = None


@dataclass
class AuthTestResult:
    """
    Owner'ın 'S5 — FINAL PROVIDER / DELIVERY GATE' STEP 1 talebinin dönüş
    tipi — YALNIZ TLS/sertifika/AUTH sonucu, e-posta gönderimiyle İLGİSİZ.
    """
    tls_ok: bool
    certificate_ok: bool
    auth_ok: bool
    error_detail: Optional[str] = None


class OutboundMailProvider(ABC):
    """
    Owner: gelecekte farklı bir sağlayıcıya geçilebilsin diye soyutlama.
    Microsoft Graph/Gmail API/OAuth adapter YAZILMAYACAK (owner kararı) —
    bu arayüz yalnız SMTP-türevi genişlemeler için tutulur.
    """

    @abstractmethod
    def send(
        self,
        *,
        to_email: str,
        subject: str,
        body_text: str,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        raise NotImplementedError


def _assert_safe_header_value(value: str, field_name: str) -> None:
    if any(ch in value for ch in _FORBIDDEN_HEADER_CHARS):
        raise OutboundMailProviderError(
            "HEADER_INJECTION_REJECTED",
            f"{field_name} alanında satır sonu karakteri (CR/LF) tespit edildi — reddedildi.",
        )


class SmtpMailProvider(OutboundMailProvider):
    """
    Authenticated SMTP — owner'ın doğrulanmış cPanel hosting mailbox'ı.
    Tüm transport parametreleri settings'ten okunur (bkz. modül docstring'i).
    """

    def __init__(self) -> None:
        self._host = settings.outreach_smtp_host
        self._port = settings.outreach_smtp_port
        self._security = settings.outreach_smtp_security  # starttls | implicit_tls
        self._username = settings.outreach_smtp_username
        self._password = settings.outreach_smtp_password
        self._sender_email = settings.outreach_sender_email or self._username

    @property
    def is_configured(self) -> bool:
        return bool(self._host and self._username and self._password and self._sender_email)

    def test_authentication(self) -> AuthTestResult:
        """
        Owner'ın 'S5 — FINAL PROVIDER / DELIVERY GATE' STEP 1 talebi: YALNIZ
        TCP → EHLO → STARTTLS → EHLO → AUTH → QUIT. MAIL FROM/RCPT TO/DATA
        HİÇBİR ZAMAN çağrılmaz — bu metod send()'İN AKSİNE bir EmailMessage
        oluşturmaz/göndermez; ayrı, bilerek SINIRLI bir kod yoludur (send()
        ile PAYLAŞILAN gövde YOK — bir e-posta gönderme İHTİMALİ bile
        mimari olarak yok).

        `with smtplib.SMTP(...) as smtp:` bloğu çıkışta otomatik QUIT
        gönderir (smtplib'in kendi __exit__ davranışı) — ayrıca çağrılmaz.

        Parola/kimlik bilgisi dönüş değerinde veya herhangi bir log
        satırında ASLA yer almaz — yalnız PASS/FAIL + (varsa) sunucunun
        genel hata kodu.

        UYARI (owner'ın 'S5 PRE-DELIVERY HARDENING' talimatı, 10.08):
        `smtp.set_debuglevel(1)` (veya başka bir protokol-seviyesi debug
        logging) BURAYA ASLA EKLENMEMELİ — AUTH komutunun taşıdığı base64
        kimlik materyali stdout/transcript'e SIZABİLİR. Bu fonksiyon
        BİLEREK yalnız yapılandırılmış logger.* çağrıları (varsa) veya
        AuthTestResult dönüş değerini kullanır, ham smtplib transcript'i
        HİÇBİR YERE yazdırılmaz.

        Çağrıldığı yerler:
        - (owner'ın açık STEP 1 talimatıyla) tek seferlik doğrulama
          script'i — HENÜZ hiçbir endpoint/otomatik akış BUNU çağırmaz.
        """
        if not (self._host and self._username and self._password):
            return AuthTestResult(tls_ok=False, certificate_ok=False, auth_ok=False, error_detail="CONFIG_INCOMPLETE")
        if self._security not in ("starttls", "implicit_tls"):
            return AuthTestResult(tls_ok=False, certificate_ok=False, auth_ok=False, error_detail="CONFIG_INVALID")

        try:
            context = ssl.create_default_context()  # sertifika doğrulaması AÇIK, asla zayıflatılmaz
            if self._security == "implicit_tls":
                with smtplib.SMTP_SSL(self._host, self._port, timeout=_CONNECT_TIMEOUT_SECONDS, context=context) as smtp:
                    smtp.login(self._username, self._password)
            else:
                with smtplib.SMTP(self._host, self._port, timeout=_CONNECT_TIMEOUT_SECONDS) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self._username, self._password)
            return AuthTestResult(tls_ok=True, certificate_ok=True, auth_ok=True)

        except smtplib.SMTPAuthenticationError as e:
            return AuthTestResult(
                tls_ok=True, certificate_ok=True, auth_ok=False,
                error_detail=f"AUTH_FAILED (sunucu kodu {e.smtp_code})",
            )
        except (smtplib.SMTPNotSupportedError, ssl.SSLError) as e:
            return AuthTestResult(tls_ok=False, certificate_ok=False, auth_ok=False, error_detail=f"TLS_FAILED: {type(e).__name__}")
        except (TimeoutError, OSError) as e:
            return AuthTestResult(tls_ok=False, certificate_ok=False, auth_ok=False, error_detail=f"CONNECTION_FAILED: {type(e).__name__}")
        except smtplib.SMTPException as e:
            return AuthTestResult(tls_ok=True, certificate_ok=False, auth_ok=False, error_detail=f"SMTP_ERROR: {type(e).__name__}")

    def send(
        self,
        *,
        to_email: str,
        subject: str,
        body_text: str,
        reply_to: Optional[str] = None,
    ) -> SendResult:
        if not self.is_configured:
            return SendResult(
                success=False,
                error_code="CONFIG_INCOMPLETE",
                error_detail="SMTP host/username/password/sender_email eksik (backend/.env OUTREACH_SMTP_*).",
            )
        if self._security not in ("starttls", "implicit_tls"):
            return SendResult(
                success=False,
                error_code="CONFIG_INVALID",
                error_detail=f"OUTREACH_SMTP_SECURITY geçersiz: {self._security!r} (starttls|implicit_tls olmalı).",
            )

        try:
            _assert_safe_header_value(to_email, "to_email")
            _assert_safe_header_value(subject, "subject")
            if reply_to:
                _assert_safe_header_value(reply_to, "reply_to")
        except OutboundMailProviderError as e:
            return SendResult(success=False, error_code=e.error_code, error_detail=e.detail)

        msg = EmailMessage()
        msg["From"] = self._sender_email
        msg["To"] = to_email
        msg["Subject"] = subject
        msg["Message-ID"] = make_msgid(domain=self._sender_email.split("@", 1)[-1])
        if reply_to:
            msg["Reply-To"] = reply_to
        msg.set_content(body_text)

        try:
            context = ssl.create_default_context()
            if self._security == "implicit_tls":
                with smtplib.SMTP_SSL(
                    self._host, self._port, timeout=_CONNECT_TIMEOUT_SECONDS, context=context
                ) as smtp:
                    smtp.login(self._username, self._password)
                    smtp.send_message(msg)
            else:  # starttls (varsayılan/tercih edilen — owner kararı: 587+STARTTLS)
                with smtplib.SMTP(self._host, self._port, timeout=_CONNECT_TIMEOUT_SECONDS) as smtp:
                    smtp.ehlo()
                    smtp.starttls(context=context)
                    smtp.ehlo()
                    smtp.login(self._username, self._password)
                    smtp.send_message(msg)

            return SendResult(success=True, provider_message_id=msg["Message-ID"])

        except smtplib.SMTPAuthenticationError as e:
            # NOT: parola/istemci mesajı burada BİLEREK loglanmıyor — yalnız
            # sunucunun döndürdüğü genel hata kodu SendResult'a konur.
            logger.error("SMTP kimlik doğrulama hatası (kimlik bilgileri LOGLANMADI)")
            return SendResult(success=False, error_code="AUTH_FAILED", error_detail=str(e.smtp_error))
        except smtplib.SMTPRecipientsRefused as e:
            return SendResult(success=False, error_code="RECIPIENT_REFUSED", error_detail=str(e.recipients))
        except smtplib.SMTPSenderRefused as e:
            return SendResult(success=False, error_code="SENDER_REFUSED", error_detail=str(e.smtp_error))
        except smtplib.SMTPDataError as e:
            return SendResult(success=False, error_code="MESSAGE_REJECTED", error_detail=str(e.smtp_error))
        except smtplib.SMTPNotSupportedError as e:
            return SendResult(success=False, error_code="TLS_FAILED", error_detail=str(e))
        except smtplib.SMTPConnectError as e:
            return SendResult(success=False, error_code="CONNECTION_FAILED", error_detail=str(e))
        except ssl.SSLError as e:
            # NOT: ssl.SSLError, Python'da OSError'un alt sınıfıdır — bu
            # yüzden aşağıdaki (TimeoutError, OSError) yakalayıcısından
            # ÖNCE burada olmalı (aksi halde bu dal asla ÇALIŞMAZ).
            return SendResult(success=False, error_code="TLS_FAILED", error_detail=str(e))
        except (TimeoutError, OSError) as e:
            # socket.timeout / socket.gaierror (DNS) / ConnectionRefusedError
            # hepsi OSError alt sınıfıdır.
            return SendResult(success=False, error_code="CONNECTION_FAILED", error_detail=str(e))
        except smtplib.SMTPException as e:
            return SendResult(success=False, error_code="UNKNOWN_SMTP_ERROR", error_detail=str(e))


_provider_singleton: Optional[SmtpMailProvider] = None


def get_outbound_mail_provider() -> OutboundMailProvider:
    """
    Owner'ın SMTP kararına göre tek V1 provider'ı döner. service.py bu
    fonksiyonu çağırır — hiçbir yerde doğrudan SmtpMailProvider() ile
    somutlaştırma yapılmaz (gelecekte sağlayıcı değişirse tek nokta).
    """
    global _provider_singleton
    if _provider_singleton is None:
        _provider_singleton = SmtpMailProvider()
    return _provider_singleton
