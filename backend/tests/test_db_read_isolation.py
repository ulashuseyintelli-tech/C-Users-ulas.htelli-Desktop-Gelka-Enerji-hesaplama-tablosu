"""Orphan-thread × request-Session yarış izolasyonu — birim + uç testleri.

Yapısal bulgu (PR #62 pre-merge incelemesi, Python 3.13.14 ile ampirik doğrulandı):
    Kritik-yol DB okumaları `_get_wrapper("db_primary"|"db_replica").call(...)` →
    `asyncio.to_thread` ile koşar. Wrapper `asyncio.wait_for(fn, timeout)` kullanır;
    zaman aşımında `to_thread` worker THREAD'İ İPTAL EDİLEMEZ → orphan arka planda
    devam eder. Okuma REQUEST Session ile yapılırsa, orphan ile `get_db`'nin
    finally-close'u arasında iş parçacıkları-arası + kapalı-Session yarışı doğar.

Düzeltme (bu değişiklik): tüm kritik-yol okuma uçları okumayı
    `run_read_in_isolated_session(db.get_bind(), reader)` ile engine'e bağlı KISA
    ÖMÜRLÜ izole Session'da yapar → orphan request Session'a dokunamaz.

Bu testler HEM birim düzeyinde yardımcının izolasyon+close davranışını, HEM de
her uç için çalışma-zamanı davranışını (geciken DB → 504 DEPENDENCY_TIMEOUT VE
orphan'a request Session DEĞİL engine geçirilir VE request Session sağlam kalır)
doğrular. Metin araması değil, gerçek wrapper (wait_for+to_thread) ile koşar.
"""

import contextlib
import time
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture(autouse=True)
def _cb_izolasyonu():
    """Circuit breaker registry process-local SINGLETON'dır: bu modülün zaman aşımı
    testleri db_primary/db_replica CB'sine record_failure() saydırır. İzole edilmezse
    biriken hatalar sonraki test modüllerini (aynı süreçte) 503 CIRCUIT_OPEN'a düşürür.
    Her testten önce ve sonra global CB registry'yi sıfırlayarak bu sızıntıyı önle.
    """
    import app.main as m

    def _sifirla():
        try:
            m._get_cb_registry().reset_all()
        except Exception:
            pass

    _sifirla()
    yield
    _sifirla()


# ══════════════════════════════════════════════════════════════════════════════
# BİRİM: run_read_in_isolated_session
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def motor_ve_session():
    """Bellek içi SQLite + tek tablo + bir kayıt; ayrı bir 'request' Session'ı."""
    from app.database import Base, MarketReferencePrice

    motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(motor, tables=[MarketReferencePrice.__table__])
    req = sessionmaker(bind=motor, autoflush=False, autocommit=False)()
    req.add(MarketReferencePrice(price_type="PTF", period="2026-05", ptf_tl_per_mwh=590.90,
                                 yekdem_tl_per_mwh=1306.10, status="provisional",
                                 source="manual_override"))
    req.commit()
    yield motor, req
    req.close()


def test_izole_session_farkli_nesne_ve_dogru_engine(motor_ve_session):
    from app.db_read_isolation import run_read_in_isolated_session

    motor, req = motor_ve_session
    gorulen = {}

    def reader(s: Session):
        gorulen["session"] = s
        gorulen["ayni_engine"] = s.get_bind() is motor
        return s.get_bind() is motor

    sonuc = run_read_in_isolated_session(req.get_bind(), reader)
    assert sonuc is True
    # reader'a verilen Session, request Session'ın KENDİSİ DEĞİL.
    assert gorulen["session"] is not req
    # Ama AYNI engine'e bağlı (aynı veriyi okur).
    assert gorulen["ayni_engine"] is True


def test_izole_session_finally_de_kapatilir(motor_ve_session):
    """Okuma sonrası izole Session KAPATILIR (açık işlem bırakmaz)."""
    from app.database import MarketReferencePrice
    from app.db_read_isolation import run_read_in_isolated_session

    motor, req = motor_ve_session
    tutulan = {}

    def reader(s: Session):
        tutulan["s"] = s
        return s.query(MarketReferencePrice).count()  # işlem başlatır (autobegin)

    n = run_read_in_isolated_session(req.get_bind(), reader)
    assert n == 1
    # finally-close çalıştı → izole Session'da açık işlem KALMADI.
    assert tutulan["s"].in_transaction() is False


def test_izole_session_reader_hata_verse_de_kapatilir(motor_ve_session):
    """reader istisna atsa bile izole Session finally'de kapatılır ve istisna yükselir."""
    from app.database import MarketReferencePrice
    from app.db_read_isolation import run_read_in_isolated_session

    motor, req = motor_ve_session
    tutulan = {}

    class Patla(RuntimeError):
        pass

    def reader(s: Session):
        tutulan["s"] = s
        s.query(MarketReferencePrice).count()  # işlem başlat
        raise Patla("okuma sırasında hata")

    with pytest.raises(Patla):
        run_read_in_isolated_session(req.get_bind(), reader)
    assert tutulan["s"].in_transaction() is False
    # request Session hâlâ sağlam.
    assert req.query(MarketReferencePrice).count() == 1


def test_izole_session_request_sessiona_dokunmaz(motor_ve_session):
    """İzole okuma request Session'ın kimliğini/işlemini kullanmaz."""
    from app.database import MarketReferencePrice
    from app.db_read_isolation import run_read_in_isolated_session

    motor, req = motor_ve_session
    run_read_in_isolated_session(
        req.get_bind(),
        lambda s: s.query(MarketReferencePrice).all(),
    )
    # request Session hâlâ kullanılabilir.
    assert req.query(MarketReferencePrice).count() == 1


# ══════════════════════════════════════════════════════════════════════════════
# UÇ: geciken DB → 504 + orphan izolasyonu (her kritik-yol okuma ucu için)
# ══════════════════════════════════════════════════════════════════════════════

@pytest.fixture()
def uc_ortami():
    """TestClient + get_db override eden 'request' Session (engine'e bağlı)."""
    from app.database import Base, MarketReferencePrice, PriceChangeHistory, get_db
    from app.main import app as fastapi_app

    motor = create_engine("sqlite://", connect_args={"check_same_thread": False},
                          poolclass=StaticPool)
    Base.metadata.create_all(
        motor, tables=[MarketReferencePrice.__table__, PriceChangeHistory.__table__])
    req = sessionmaker(bind=motor, autoflush=False, autocommit=False)()
    req.add(MarketReferencePrice(price_type="PTF", period="2026-05", ptf_tl_per_mwh=590.90,
                                 yekdem_tl_per_mwh=1306.10, status="provisional",
                                 source="manual_override"))
    req.commit()

    fastapi_app.dependency_overrides[get_db] = lambda: req
    yield TestClient(fastapi_app), req, motor, fastapi_app
    fastapi_app.dependency_overrides.clear()
    req.close()


def _gecikmeli_ve_izole_dogrula(uc_ortami, istek, ek_yamalar=()):
    """Ortak: run_read_in_isolated_session'ı yavaş+gözlemci ile değiştir; isteği koştur.

    Doğrular:
      - 504 DEPENDENCY_TIMEOUT (gerçek wrapper wait_for zaman aşımı → tutarlı eşleme),
      - orphan'a geçirilen bind = request Session'ın engine'i, request Session DEĞİL,
      - reader gerçekten çağrılabilir bir okuma kapanışı,
      - zaman aşımından sonra request Session hâlâ sağlam/kullanılabilir.

    Determinizm için retry 0'a çekilir (tek deneme, tek zaman aşımı, tek orphan) ve
    db zaman aşımı kısa tutulur.
    """
    import app.guard_config as gc
    import app.main as m
    from app.database import MarketReferencePrice

    _client, req, motor, _app = uc_ortami
    gozlem = {}
    gercek = m.run_read_in_isolated_session

    def yavas(bind, reader):
        gozlem["bind_request_session_mu"] = bind is req
        gozlem["bind_engine_mi"] = bind is motor
        gozlem["reader_cagrilabilir"] = callable(reader)
        time.sleep(0.4)  # db timeout (0.15) < sleep → gerçek TimeoutError + orphan
        return gercek(bind, reader)

    with contextlib.ExitStack() as yigin:
        yigin.enter_context(patch.object(
            gc.GuardConfig, "get_timeout_for_dependency", lambda self, dep: 0.15))
        yigin.enter_context(patch.object(
            gc.GuardConfig, "get_retry_max_attempts_for_dependency", lambda self, dep: 0))
        yigin.enter_context(patch.object(m, "run_read_in_isolated_session", yavas))
        for y in ek_yamalar:
            yigin.enter_context(y)
        yanit = istek(_client)

    assert yanit.status_code == 504, yanit.text
    assert yanit.json()["detail"]["error_code"] == "DEPENDENCY_TIMEOUT"
    # Orphan'a request Session DEĞİL, engine geçirildi → request Session'a dokunulamaz.
    assert gozlem.get("bind_request_session_mu") is False
    assert gozlem.get("bind_engine_mi") is True
    assert gozlem.get("reader_cagrilabilir") is True
    # Request Session zaman aşımından sonra hâlâ sağlam.
    assert req.query(MarketReferencePrice).count() == 1
    time.sleep(0.5)  # orphan kendi izole Session'ında bitsin (request Session'a değmeden)


def test_uc_calculate_offer_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.post("/calculate-offer", json={"extraction": {}}),
    )


def test_uc_market_prices_liste_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/admin/market-prices",
                        params={"page": 1, "page_size": 50, "price_type": "PTF"}),
    )


def test_uc_market_prices_history_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/admin/market-prices/history",
                        params={"period": "2026-05", "price_type": "PTF"}),
    )


def test_uc_approval_candidate_gecikme_504_izole(uc_ortami):
    import app.main as m

    async def _sahte_resmi(kalem, period, segment):
        return {"kalem": kalem, "period": period, "value": 590.90}

    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/admin/market-prices/approval-candidate",
                        params={"period": "2026-05", "kalem": "PTF"}),
        ek_yamalar=[patch.object(m, "_resmi_aday_cek", _sahte_resmi)],
    )


def test_uc_approvals_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/admin/market-prices/approvals", params={"period": "2026-05"}),
    )


def test_uc_market_price_period_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/admin/market-prices/2026-05"),
    )


def test_uc_import_preview_gecikme_504_izole(uc_ortami):
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.post(
            "/admin/market-prices/import/preview",
            files={"file": ("p.csv", "period,value,status\n2026-05,600,provisional\n", "text/csv")},
            data={"price_type": "PTF"},
        ),
    )


def test_uc_market_price_lookup_replica_gecikme_504_izole(uc_ortami):
    # db_replica ucu — aynı DBClientWrapper mekanizması, aynı izolasyon deseni.
    _gecikmeli_ve_izole_dogrula(
        uc_ortami,
        lambda c: c.get("/api/market-prices/PTF/2026-05"),
    )
