"""
S5 — Outreach — sender_profile.py

Owner'ın 10.08 düzeltmesi madde 5-6: zorunlu gönderici tanıtıcı bilgileri
ALICIYA (recipient_legal_type) göre DEĞİL, GÖNDERİCİYE (Gelka'nın kendi
doğrulanmış kurumsal kimliği) göre belirlenir. Ticaret Bakanlığı kuralı:
tacir gönderici → MERSİS + ticaret unvanı zorunlu; ayrıca erişilebilir
iletişim bilgisi + kolay/ücretsiz ret yöntemi zorunlu. Gelka kurumsal
(tacir) bir gönderici olduğu için bu profil şeması buna göre kuruldu.

privacy_notice_url da REQUIRED sayıldı (yalnız "önerilir" değil) — çünkü
S5'in bütün senaryosu KVKK'nın "doğrudan ilgili kişiden alınmamış veri"
durumu (prospect verisi web'den keşfedildi, sahibinden toplanmadı), bu da
aydınlatma yükümlülüğünü somut biçimde devreye sokuyor (owner'ın S5 GO'sunda
atıfta bulunulan KVKK duyurusu).

Gerçek değerler (MERSİS no, tescilli unvan, gizlilik metni URL'i vb.)
BURADA veya kodun hiçbir yerinde İCAT EDİLMEZ — yalnız core/config.py
üzerinden, owner'ın machine-local .env'inden okunur (openai_api_key /
outreach_smtp_* ile AYNI desen). Boşsa fail-closed: draft üretimi
SenderProfileIncompleteError ile durur.

Çağrıldığı yerler:
- app/outreach/drafting.py (create_draft, render_mandatory_footer) [S5-WB4]
"""
from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from ..core.config import settings

_REQUIRED_FIELDS = (
    "trade_name",
    "mersis_number",
    "sender_email",
    "unsubscribe_instruction",
    "privacy_notice_url",
)


class SenderProfileIncompleteError(Exception):
    """
    Fail-closed: owner henüz gönderici profilini (config) doldurmadı.
    Draft üretimi bu hatayla durur — sahte/eksik bir yasal footer ile
    ASLA gönderilebilir bir taslak üretilmez.
    """

    def __init__(self, missing_fields: list[str]):
        self.missing_fields = missing_fields
        super().__init__(
            "Outreach sender profile eksik alan(lar): "
            + ", ".join(missing_fields)
            + " — bkz. backend/.env OUTREACH_SENDER_* ayarları."
        )


@dataclass(frozen=True)
class OutreachSenderProfile:
    trade_name: Optional[str]
    mersis_number: Optional[str]
    sender_email: Optional[str]
    phone: Optional[str]
    website: Optional[str]
    privacy_notice_url: Optional[str]
    unsubscribe_instruction: Optional[str]

    @property
    def missing_required_fields(self) -> list[str]:
        return [f for f in _REQUIRED_FIELDS if not getattr(self, f)]

    @property
    def is_complete(self) -> bool:
        return not self.missing_required_fields


def get_sender_profile() -> OutreachSenderProfile:
    """Owner'ın config'inden (machine-local .env) TEK doğruluk kaynağı olarak okur."""
    return OutreachSenderProfile(
        trade_name=settings.outreach_sender_trade_name,
        mersis_number=settings.outreach_sender_mersis_number,
        sender_email=settings.outreach_sender_email or settings.outreach_smtp_username,
        phone=settings.outreach_sender_phone,
        website=settings.outreach_sender_website,
        privacy_notice_url=settings.outreach_sender_privacy_notice_url,
        unsubscribe_instruction=settings.outreach_sender_unsubscribe_instruction,
    )


def render_mandatory_footer(profile: OutreachSenderProfile) -> str:
    """
    DETERMİNİSTİK — AI bu bloğu ASLA üretmez/değiştirmez (owner madde 7:
    "Mandatory legal footer AI'ya bırakılmaz"). SYSTEM/immutable blok.

    Raises:
        SenderProfileIncompleteError: profil eksikse (fail-closed).
    """
    if not profile.is_complete:
        raise SenderProfileIncompleteError(profile.missing_required_fields)

    contact_line = f"İletişim: {profile.sender_email}"
    if profile.phone:
        contact_line += f" · {profile.phone}"

    lines = [
        "---",
        profile.trade_name,
        f"MERSİS No: {profile.mersis_number}",
        contact_line,
    ]
    if profile.website:
        lines.append(profile.website)
    lines.append(f"Aydınlatma metni: {profile.privacy_notice_url}")
    lines.append(profile.unsubscribe_instruction)
    lines.append(
        "Bu ileti ticari elektronik iletidir; ret bildiriminiz derhal işleme alınır."
    )
    return "\n".join(lines)
