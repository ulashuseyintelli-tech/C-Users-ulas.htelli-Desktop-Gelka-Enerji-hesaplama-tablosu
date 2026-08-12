"""
S5 — Outreach.

Prospect/mevcut müşteri hedeflerine insan-onaylı tanışma e-postası gönderim
modülü. PROSPECT ≠ SEND-ELIGIBLE (owner kararı, S5 GO): keşfedilmiş bir
e-posta adresinin var olması, o adrese gönderim YAPILABİLECEĞİ anlamına
gelmez — her gönderim, tek yetkili karar noktası olan
compliance.py::evaluate_email_send_eligibility() üzerinden geçmek
ZORUNDADIR; hiçbir endpoint bunu bypass edemez (HARD GATE).

Alt modüller:
- compliance.py   — evaluate_email_send_eligibility() (HARD GATE, tek karar noktası) [S5-WB3]
- drafting.py      — şablon + opsiyonel AI-destekli taslak üretimi (deterministic guardrail) [S5-WB4]
- smtp_provider.py — OutboundMailProvider soyutlaması + authenticated SMTP adapter [S5-WB5]
- service.py       — iş mantığı (draft/approve/send orkestrasyon) [S5-WB4+]
- schemas.py        — Pydantic request/response şemaları [S5-WB4+]
- router.py         — REST endpoint'leri (app/main.py'de include edilir) [S5-WB4+]
"""
