"""
Açılış kapısı — kontrollü ileri migration (351d314819d5 → b7e4c2d91a60) izole testleri.

Kanıtlanacak (YALNIZ sentetik, geçici dizindeki eski şemalı DB üzerinde):
- Eski tablo verisi migration sonrası şema + içerik düzeyinde korunur (kapının kendi özet
  yardımcısı değil, test içindeki bağımsız okuma ile karşılaştırılır).
- Tekrar açılış hafif sertifikasyondur; dosya değişmez.
- Migration hatası / kesintisi canonical dosyayı DEĞİŞTİRMEZ (GateRefused 54 ya da kesinti),
  kalıntı bırakmaz ve sonraki açılış temiz tamamlanır.
- Yarım kalan yayım (canonical yok + prepublish var) boş DB kurulmadan geri alınır.
- Kurcalanmış uygulama başı DB sert durur.

Canlı/kurulu DB, kurulu uygulama ya da port kullanılmaz; alembic yalnız geçici dosyaya koşar.
"""
from __future__ import annotations

import ast
import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
import sys
import time

import pytest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app.legacy_adoption import alembic_runner as ar  # noqa: E402
from app.legacy_adoption import startup_gate as sg  # noqa: E402
from app.legacy_adoption.lineage import CANONICAL_HEAD  # noqa: E402

pytestmark = [
    pytest.mark.skipif(not ar.is_alembic_available(), reason="alembic calistirilabiliri yok"),
    # Yayım yazma kilidi (yazmayı reddeden tutamak + POSIX atomik değiştirme) yalnız Windows/NTFS.
    pytest.mark.skipif(os.name != "nt", reason="ileri migration yalniz Windows'ta uygulanir"),
]

_BACKEND = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        h.update(fh.read())
    return h.hexdigest()


def _revizyonlar(path: str) -> tuple:
    con = sqlite3.connect(path)
    try:
        return tuple(r[0] for r in con.execute("SELECT version_num FROM alembic_version"))
    finally:
        con.close()


def _tum_tablo_icerikleri(path: str, haric=()) -> dict:
    """Bağımsız okuma: her tablonun şeması + tüm satırları (rowid sıralı)."""
    con = sqlite3.connect(path)
    try:
        cikti = {}
        for ad, sql in con.execute("SELECT name, sql FROM sqlite_master WHERE type='table' "
                                   "AND name NOT LIKE 'sqlite_%' ORDER BY name").fetchall():
            if ad in haric:
                continue
            cikti[ad] = (sql, con.execute(f'SELECT * FROM "{ad}" ORDER BY rowid').fetchall())
        return cikti
    finally:
        con.close()


def _sentetik_veri_ekle(path: str) -> None:
    """Eski şemaya sentetik (gerçek olmayan) satırlar: tarihsel fiyat kayıtları + teklifler."""
    con = sqlite3.connect(path)
    try:
        for satir in (("2099-01", 2500.0, 50.0, "epias_manual", "final", 0),
                      ("2099-02", 2600.0, 0.0, "seed", "provisional", 1)):
            con.execute(
                "INSERT INTO market_reference_prices (period, price_type, ptf_tl_per_mwh, yekdem_tl_per_mwh, "
                "source, status, is_locked, created_at, updated_at) "
                "VALUES (?, 'PTF', ?, ?, ?, ?, ?, '2099-01-01 00:00:00', '2099-01-01 00:00:00')", satir)
        for i in range(3):
            con.execute(
                "INSERT INTO offers (consumption_kwh, current_unit_price, weighted_ptf, yekdem, "
                "agreement_multiplier, current_total, offer_total, savings_amount, savings_ratio, created_at) "
                "VALUES (?,?,?,?,?,?,?,?,?,?)",
                (1000.0 + i, 3.5, 2500.0, 50.0, 1.05, 3500.0, 3300.0, 200.0, 0.057, f"2099-01-0{i + 1} 10:00:00"))
        con.commit()
    finally:
        con.close()


@pytest.fixture(scope="module")
def eski_sema_ana(tmp_path_factory) -> str:
    """Kapının KENDİ taze kurulumu ile 351d314819d5 DB + sentetik veri (modül başına bir kez)."""
    yol = str(tmp_path_factory.mktemp("ileri") / "ana" / "gelka_enerji.db")
    rapor = sg.run_startup_gate(yol)
    assert rapor.terminal_revision == CANONICAL_HEAD
    _sentetik_veri_ekle(yol)
    assert sg.classify_startup_state(yol) is sg.BootstrapState.VERIFIED_CANONICAL_HEAD
    return yol


@pytest.fixture()
def eski_db(eski_sema_ana, tmp_path) -> str:
    hedef = tmp_path / "userData" / "database" / "gelka_enerji.db"
    hedef.parent.mkdir(parents=True)
    shutil.copyfile(eski_sema_ana, hedef)
    return str(hedef)


def _kalinti_yok(canonical: str) -> None:
    for ek in (sg.ILERI_WORKING_SUFFIX, sg.ILERI_PREPUBLISH_SUFFIX, sg.ILERI_JOURNAL_SUFFIX,
               sg.ILERI_JOURNAL_SUFFIX + ".tmp", "-journal", "-wal", "-shm"):
        assert not os.path.exists(canonical + ek), f"kalinti: {ek}"


# ── Sabit kolon kümesi = uygulama metadata'sı; script başı = uygulama başı ──
def test_uygulama_basi_kolon_kumeleri_onay_metadata_ile_ayni():
    from app.price_approval import onay_metadata

    beklenen = {t.name: frozenset(c.name for c in t.columns) for t in onay_metadata.sorted_tables}
    assert beklenen == dict(sg.APPLICATION_HEAD_TABLE_COLUMNS)


def test_uygulama_basi_alembic_script_basi_ile_ayni():
    """Migration dosyalarindaki revision/down_revision zincirinin TEK basi APPLICATION_HEAD'dir.

    (backend/alembic klasoru sys.path'te alembic paketini golgeledigi icin dosyalar AST ile okunur.)
    """
    klasor = os.path.join(_BACKEND, "alembic", "versions")
    revizyonlar, ebeveynler = set(), set()
    for ad in os.listdir(klasor):
        if not ad.endswith(".py"):
            continue
        agac = ast.parse(open(os.path.join(klasor, ad), encoding="utf-8").read())
        for dugum in agac.body:
            if isinstance(dugum, ast.AnnAssign) and isinstance(dugum.target, ast.Name):
                hedef, deger = dugum.target.id, dugum.value
            elif isinstance(dugum, ast.Assign) and isinstance(dugum.targets[0], ast.Name):
                hedef, deger = dugum.targets[0].id, dugum.value
            else:
                continue
            if hedef == "revision":
                revizyonlar.add(ast.literal_eval(deger))
            elif hedef == "down_revision":
                d = ast.literal_eval(deger)
                ebeveynler.update(d if isinstance(d, (tuple, list)) else [d])
    assert revizyonlar - ebeveynler == {sg.APPLICATION_HEAD}


def test_run_server_ileri_kapiyi_cagirir():
    agac = ast.parse(open(os.path.join(_BACKEND, "run_server.py"), encoding="utf-8").read())
    cagrilar = {n.func.id for n in ast.walk(agac) if isinstance(n, ast.Call) and isinstance(n.func, ast.Name)}
    assert "run_startup_gate_ileri" in cagrilar
    assert "run_startup_gate" not in cagrilar


# ── Veri korunması + tekrar açılış ────────────────────────────────────────
def test_ileri_migration_eski_veriyi_korur_ve_onay_tablolarini_ekler(eski_db):
    once = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    assert len(once["offers"][1]) == 3 and len(once["market_reference_prices"][1]) == 2

    rapor = sg.run_startup_gate_ileri(eski_db)

    assert rapor.action == "FORWARD_MIGRATED"
    assert rapor.details["onceki_eylem"] == "CERTIFIED_NOOP"
    assert rapor.terminal_revision == sg.APPLICATION_HEAD
    assert rapor.integrity_check == "ok" and rapor.foreign_key_violations == 0
    assert _revizyonlar(eski_db) == (sg.APPLICATION_HEAD,)
    sonra = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    yeni = set(sg.APPLICATION_HEAD_TABLE_COLUMNS)
    assert set(sonra) - set(once) == yeni
    assert {t: sonra[t] for t in once} == once, "eski tablo semasi ya da verisi degisti"
    assert all(sonra[t][1] == [] for t in yeni), "migration onay satiri uretmemeli"
    assert sg.classify_startup_state(eski_db) is sg.BootstrapState.VERIFIED_APPLICATION_HEAD
    assert os.path.isfile(eski_db + sg.AUDIT_SUFFIX)
    _kalinti_yok(eski_db)


def test_tekrar_acilis_hafif_sertifikasyon_dosya_degismez(eski_db):
    sg.run_startup_gate_ileri(eski_db)
    ozet = _sha256(eski_db)
    ikinci = sg.run_startup_gate_ileri(eski_db)
    ucuncu = sg.run_startup_gate_ileri(eski_db)
    assert ikinci.action == ucuncu.action == "CERTIFIED_NOOP"
    assert ikinci.state is sg.BootstrapState.VERIFIED_APPLICATION_HEAD
    assert _sha256(eski_db) == ozet
    _kalinti_yok(eski_db)


def test_db_yoksa_taze_kurulum_uygulama_basina_ulasir(tmp_path):
    canonical = str(tmp_path / "userData" / "database" / "gelka_enerji.db")
    rapor = sg.run_startup_gate_ileri(canonical)
    assert rapor.details["onceki_eylem"] == "FRESH_INITIALIZED"
    assert rapor.terminal_revision == sg.APPLICATION_HEAD
    assert sg.run_startup_gate_ileri(canonical).action == "CERTIFIED_NOOP"


# ── Hata / kesinti: canonical değişmez, sonraki açılış tamamlanır ────────
def test_gercek_migration_hatasi_canonical_degismez_54(eski_db):
    # Gerçek hata: eski şemada çakışan aynı adlı tablo → CREATE TABLE gerçekten başarısız olur.
    con = sqlite3.connect(eski_db)
    con.execute("CREATE TABLE ptf_onay_revizyonlari (x INTEGER)")
    con.commit()
    con.close()
    assert sg.classify_startup_state(eski_db) is sg.BootstrapState.VERIFIED_CANONICAL_HEAD
    ozet = _sha256(eski_db)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_FORWARD_MIGRATION_FAILED
    assert _sha256(eski_db) == ozet
    assert _revizyonlar(eski_db) == (CANONICAL_HEAD,)
    _kalinti_yok(eski_db)


def test_eski_veriyi_degistiren_migration_yayimlanmaz(eski_db, monkeypatch):
    gercek = ar.alembic_upgrade

    def bozan_upgrade(db_path, revision, **kw):
        gercek(db_path, revision, **kw)
        con = sqlite3.connect(db_path)
        con.execute("UPDATE offers SET offer_total = offer_total + 1")
        con.commit()
        con.close()

    monkeypatch.setattr(ar, "alembic_upgrade", bozan_upgrade)
    ozet = _sha256(eski_db)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_FORWARD_MIGRATION_FAILED
    assert "offers" in str(exc.value)
    assert _sha256(eski_db) == ozet
    _kalinti_yok(eski_db)


def test_alembic_hatasi_canonical_degismez_sonra_tamamlanir(eski_db, monkeypatch):
    def patlayan(db_path, revision, **kw):
        raise RuntimeError("sentetik alembic hatasi")

    monkeypatch.setattr(ar, "alembic_upgrade", patlayan)
    ozet = _sha256(eski_db)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_FORWARD_MIGRATION_FAILED
    assert _sha256(eski_db) == ozet
    _kalinti_yok(eski_db)
    monkeypatch.undo()
    assert sg.run_startup_gate_ileri(eski_db).action == "FORWARD_MIGRATED"


@pytest.mark.parametrize("nokta", sg.ILERI_FAULT_POINTS)
def test_kesinti_canonical_yarim_birakmaz_ve_sonraki_acilis_tamamlanir(eski_db, nokta):
    once = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    ozet = _sha256(eski_db)
    with pytest.raises(sg.InjectedFault):
        sg.run_startup_gate_ileri(eski_db, fault_at=nokta)

    assert os.path.isfile(eski_db), "canonical kayboldu"
    if nokta in sg.ILERI_YAYIM_SONRASI_NOKTALAR:
        assert _revizyonlar(eski_db) == (sg.APPLICATION_HEAD,)
    else:
        assert _sha256(eski_db) == ozet and _revizyonlar(eski_db) == (CANONICAL_HEAD,)
        _kalinti_yok(eski_db)

    rapor = sg.run_startup_gate_ileri(eski_db)
    assert rapor.terminal_revision == sg.APPLICATION_HEAD
    sonra = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    assert {t: sonra[t] for t in once} == once
    _kalinti_yok(eski_db)


def test_gunluk_var_canonical_yoksa_eski_kopya_geri_konmaz_bos_db_kurulmaz(eski_db):
    # Yöntem canonical'i taşımaz/silmez; günlük varken canonical yoksa bu dışarıdan bir kayıptır.
    kaynak = _sha256(eski_db)
    sg._gunluk_yaz(eski_db, {"asama": "YEDEKLENIYOR", "kaynak_sha256": kaynak, "yeni_sha256": "0" * 64})
    os.replace(eski_db, eski_db + sg.ILERI_PREPUBLISH_SUFFIX)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_RECOVERY_AMBIGUOUS
    assert not os.path.exists(eski_db), "boş DB kurulmamalı, eski kopya geri konmamalı"
    assert _sha256(eski_db + sg.ILERI_PREPUBLISH_SUFFIX) == kaynak


def test_kurcalanmis_uygulama_basi_sert_durur(eski_db):
    sg.run_startup_gate_ileri(eski_db)
    con = sqlite3.connect(eski_db)
    con.execute("DROP TABLE yekdem_onay_revizyonlari")
    con.execute("CREATE TABLE yekdem_onay_revizyonlari (id INTEGER PRIMARY KEY, period TEXT)")
    con.commit()
    con.close()
    ozet = _sha256(eski_db)
    assert sg.classify_startup_state(eski_db) is sg.BootstrapState.UNKNOWN_SCHEMA
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_HARD_STOP_UNKNOWN_SCHEMA
    assert _sha256(eski_db) == ozet


def test_bilinmeyen_ileri_revizyon_sert_durur(eski_db):
    con = sqlite3.connect(eski_db)
    con.execute("UPDATE alembic_version SET version_num='ffffffffffff'")
    con.commit()
    con.close()
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_HARD_STOP_UNKNOWN_SCHEMA


def test_legacy_013_benimseme_sonrasi_uygulama_basina_ulasir(tmp_path):
    """Kurulu sürümlerin eski yolu: 013 legacy → rescue → benimseme → ileri migration."""
    from test_legacy_adoption_validator import _GOLDEN_FIXTURE_ROW_COUNTS, _build_golden_legacy_db
    from app.legacy_adoption.rescue import perform_rescue

    kaynak = tmp_path / "app" / "resources" / "backend"
    kaynak.mkdir(parents=True)
    legacy = _build_golden_legacy_db(str(kaynak / "gelka_enerji.db"))
    userdata = tmp_path / "AppData" / "Roaming" / "gelka-enerji" / "database"
    canonical = str(userdata / "gelka_enerji.db")
    perform_rescue(legacy, canonical, str(userdata / "backups"), version_label="1.0.6",
                   confirm_installer_context=True)

    rapor = sg.run_startup_gate_ileri(canonical)
    assert rapor.details["onceki_eylem"] == "ADOPTED"
    assert rapor.terminal_revision == sg.APPLICATION_HEAD
    for tablo, sayi in _GOLDEN_FIXTURE_ROW_COUNTS.items():
        assert rapor.row_counts.get(tablo) == sayi, tablo
    assert sg.run_startup_gate_ileri(canonical).action == "CERTIFIED_NOOP"


# ═══════════════════════════════════════════════════════════════════════════
# DB değiştirme yolu güvenliği (GO "yerel kapanış" madde 3) — yalnız eksik riskler
# ═══════════════════════════════════════════════════════════════════════════

_BACKEND_KOK = _BACKEND


def _cocuk(kod: str, *, bekle=True, timeout=240):
    """Ayrı Python süreci (gerçek süreçler arası kilit / gerçek çökme için)."""
    tam = "import sys; sys.path.insert(0, %r)\n" % _BACKEND_KOK + kod
    if bekle:
        return subprocess.run([sys.executable, "-c", tam], cwd=_BACKEND_KOK, capture_output=True,
                              text=True, timeout=timeout)
    return subprocess.Popen([sys.executable, "-c", tam], cwd=_BACKEND_KOK, stdout=subprocess.PIPE,
                            stderr=subprocess.PIPE, text=True)


@pytest.mark.parametrize("ek", ["-journal", "-wal", "-shm"])
def test_sqlite_yan_dosyasi_varken_kopyalanmaz_ve_dosyalara_dokunulmaz(eski_db, ek):
    with open(eski_db + ek, "wb") as fh:
        fh.write(b"")  # boş yan dosya bile (açık bağlantı/yarım işlem işareti) yeterli
    ozet = _sha256(eski_db)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_CONCURRENT_OR_BUSY
    assert _sha256(eski_db) == ozet and os.path.getsize(eski_db + ek) == 0
    assert _revizyonlar(eski_db) == (CANONICAL_HEAD,)
    for yol in (sg.ILERI_WORKING_SUFFIX, sg.ILERI_PREPUBLISH_SUFFIX, sg.ILERI_JOURNAL_SUFFIX):
        assert not os.path.exists(eski_db + yol)


@pytest.mark.parametrize("islemde", [True, False])
def test_acik_yazici_baglanti_varken_migration_baslamaz(eski_db, islemde):
    tutucu = sqlite3.connect(eski_db, isolation_level=None)
    if islemde:
        tutucu.execute("BEGIN IMMEDIATE")  # yazıcı kilidi; yan dosya oluşturmaz
    else:
        tutucu.execute("SELECT COUNT(*) FROM offers").fetchall()  # boşta ama yazma erişimli bağlantı
    try:
        assert not os.path.exists(eski_db + "-journal")
        ozet = _sha256(eski_db)
        with pytest.raises(sg.GateRefused) as exc:
            sg.run_startup_gate_ileri(eski_db)
        assert exc.value.exit_code == sg.EXIT_CONCURRENT_OR_BUSY
        assert "YAZMA erisimiyle acik" in str(exc.value)
        assert _sha256(eski_db) == ozet
        assert not os.path.exists(eski_db + sg.ILERI_WORKING_SUFFIX)
    finally:
        if islemde:
            tutucu.execute("ROLLBACK")
        tutucu.close()
    assert sg.run_startup_gate_ileri(eski_db).action == "FORWARD_MIGRATED"


_YAZICI = (
    "import sqlite3, sys, json\n"
    "try:\n"
    "    c = sqlite3.connect(sys.argv[1], timeout=1)\n"
    "    c.execute(\"INSERT INTO offers (consumption_kwh, current_unit_price, weighted_ptf, yekdem, "
    "agreement_multiplier, current_total, offer_total, savings_amount, savings_ratio, created_at) "
    "VALUES (424242,1,1,1,1,1,1,1,0,'2099-02-02')\")\n"
    "    c.commit(); c.close(); print(json.dumps({'yazdi': True}))\n"
    "except Exception as e:\n"
    "    print(json.dumps({'yazdi': False, 'hata': str(e)}))\n"
)


def _ayri_surec_yazici(db: str) -> dict:
    r = subprocess.run([sys.executable, "-c", _YAZICI, db], capture_output=True, text=True, timeout=120)
    return json.loads(r.stdout.strip().splitlines()[-1])


def _isaret_sayisi(db: str) -> int:
    con = sqlite3.connect(db)
    try:
        return con.execute("SELECT COUNT(*) FROM offers WHERE consumption_kwh = 424242").fetchone()[0]
    finally:
        con.close()


@pytest.mark.parametrize("nokta", ["ileri_calisma_kopyasi_oncesi", "ileri_migration_sonrasi", "ileri_yayim_oncesi",
                                   "ileri_son_sha_sonrasi", "ileri_gunluk_yazildi", "ileri_yedek_kopyalandi",
                                   "ileri_yayim_gunluk_oncesi", "ileri_yedek_silme_oncesi", "ileri_yayim_sonrasi"])
def test_yayim_sinirinda_ayri_surec_yazici_engellenir_veri_kaybolmaz(eski_db, monkeypatch, nokta):
    """Kontrollü yazıcı TAM bu noktada ayrı süreçte commit dener (özellikle son SHA → değiştirme aralığı).

    Beklenen: yazma REDDEDİLİR (SHA denetimi kilit sayılmaz; koruma yazmayı reddeden tutamaktır),
    geçiş tamamlanır, eski veri korunur; kilit bırakılınca aynı yazıcı yeni DB'ye yazabilir.
    """
    once = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    gercek = sg._fault
    gozlem = {}

    def kanca(n, fault_at):
        if n == nokta and "yazici" not in gozlem:
            gozlem["yazici"] = _ayri_surec_yazici(eski_db)
        return gercek(n, fault_at)

    monkeypatch.setattr(sg, "_fault", kanca)
    rapor = sg.run_startup_gate_ileri(eski_db)
    monkeypatch.undo()
    assert rapor.action == "FORWARD_MIGRATED"
    assert gozlem["yazici"]["yazdi"] is False, gozlem
    assert _isaret_sayisi(eski_db) == 0, "reddedilen yazma sessizce görünmemeli"
    sonra = _tum_tablo_icerikleri(eski_db, haric=("alembic_version", "offers"))
    assert {t: sonra[t] for t in once if t != "offers"} == {t: v for t, v in once.items() if t != "offers"}
    assert len(_tum_tablo_icerikleri(eski_db)["offers"][1]) == len(once["offers"][1])
    _kalinti_yok(eski_db)
    # Kilit bırakıldı: aynı yazıcı artık yeni (uygulama başı) DB'ye yazar ve veri kalır.
    assert _ayri_surec_yazici(eski_db) == {"yazdi": True}
    assert _isaret_sayisi(eski_db) == 1 and _revizyonlar(eski_db) == (sg.APPLICATION_HEAD,)


def test_degistirme_aninda_yabanci_okuyucu_varsa_canonical_degismez(eski_db, monkeypatch):
    gercek = sg._fault
    okuyucu = {}

    def kanca(n, fault_at):
        if n == "ileri_yedek_kopyalandi":
            okuyucu["con"] = sqlite3.connect("file:" + eski_db.replace("\\", "/") + "?mode=ro", uri=True)
            okuyucu["con"].execute("SELECT COUNT(*) FROM offers").fetchall()
        return gercek(n, fault_at)

    ozet = _sha256(eski_db)
    monkeypatch.setattr(sg, "_fault", kanca)
    try:
        with pytest.raises(sg.GateRefused) as exc:
            sg.run_startup_gate_ileri(eski_db)
    finally:
        okuyucu["con"].close()
    monkeypatch.undo()
    assert exc.value.exit_code == sg.EXIT_CONCURRENT_OR_BUSY and "atomik degistirme" in str(exc.value)
    assert _sha256(eski_db) == ozet and _revizyonlar(eski_db) == (CANONICAL_HEAD,)
    _kalinti_yok(eski_db)
    assert sg.run_startup_gate_ileri(eski_db).action == "FORWARD_MIGRATED"


def test_eszamanli_ikinci_acilis_ayri_surecte_engellenir(eski_db, tmp_path):
    hazir, dur = tmp_path / "hazir", tmp_path / "dur"
    cocuk = _cocuk(
        "import time, pathlib\n"
        "from app.legacy_adoption import startup_gate as sg\n"
        f"with sg.acilis_kilidi({eski_db!r}):\n"
        f"    pathlib.Path({str(hazir)!r}).write_text('1')\n"
        f"    while not pathlib.Path({str(dur)!r}).exists(): time.sleep(0.05)\n", bekle=False)
    try:
        for _ in range(400):
            if hazir.exists() or cocuk.poll() is not None:
                break
            time.sleep(0.05)
        assert hazir.exists(), cocuk.communicate(timeout=5)
        ozet = _sha256(eski_db)
        with pytest.raises(sg.GateRefused) as exc:
            sg.run_startup_gate_ileri(eski_db)
        assert exc.value.exit_code == sg.EXIT_CONCURRENT_OR_BUSY
        assert _sha256(eski_db) == ozet and not os.path.exists(eski_db + sg.ILERI_WORKING_SUFFIX)
    finally:
        dur.write_text("1")
        cocuk.communicate(timeout=30)
    # Kilit tutan süreç çıkınca (bayat kilit dosyası kalsa bile) açılış ilerler.
    assert os.path.exists(eski_db + sg.ILERI_LOCK_SUFFIX)
    assert sg.run_startup_gate_ileri(eski_db).action == "FORWARD_MIGRATED"


def test_iki_sureç_ayni_anda_acilirsa_tek_gecis_olur_veri_korunur(eski_db):
    once = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    kod = ("import json\n"
           "from app.legacy_adoption import startup_gate as sg\n"
           "try:\n"
           f"    r = sg.run_startup_gate_ileri({eski_db!r})\n"
           "    print(json.dumps({'eylem': r.action}))\n"
           "except sg.GateRefused as e:\n"
           "    print(json.dumps({'ret': e.exit_code}))\n")
    surecler = [_cocuk(kod, bekle=False) for _ in range(3)]
    sonuclar = []
    for p in surecler:
        cikti, hata = p.communicate(timeout=240)
        assert p.returncode == 0, hata
        sonuclar.append(json.loads(cikti.strip().splitlines()[-1]))
    gecis = [x for x in sonuclar if x.get("eylem") == "FORWARD_MIGRATED"]
    assert len(gecis) == 1, sonuclar
    assert all(x in ({"eylem": "FORWARD_MIGRATED"}, {"eylem": "CERTIFIED_NOOP"}, {"ret": 55}) for x in sonuclar)
    assert _revizyonlar(eski_db) == (sg.APPLICATION_HEAD,)
    sonra = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    assert {t: sonra[t] for t in once} == once
    _kalinti_yok(eski_db)


def test_yayim_dosyalari_ayni_klasor_ve_birimde_degilse_yayim_yok(eski_db, monkeypatch):
    working, prepublish = sg._ileri_yollar(eski_db)
    assert os.path.dirname(working) == os.path.dirname(prepublish) == os.path.dirname(eski_db)
    assert os.path.dirname(sg._gunluk_yolu(eski_db)) == os.path.dirname(eski_db)
    monkeypatch.setattr(sg, "_ayni_birimde", lambda a, b: False)
    ozet = _sha256(eski_db)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert "ayni klasor/birimde degil" in str(exc.value)
    assert _sha256(eski_db) == ozet
    _kalinti_yok(eski_db)


@pytest.mark.parametrize("nokta", ["ileri_gunluk_yazildi", "ileri_yedek_kopyalandi",
                                   "ileri_yayim_gunluk_oncesi", "ileri_yedek_silme_oncesi"])
def test_gercek_surec_cokmesi_sonrasi_kimlige_bagli_kurtarma(eski_db, nokta):
    """Çocuk süreç os._exit ile ÇÖKER (except/finally çalışmaz); sonraki açılış doğru toparlar."""
    once = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    kod = ("import os\n"
           "from app.legacy_adoption import startup_gate as sg\n"
           "gercek = sg._fault\n"
           f"def cok(nokta, fault_at):\n"
           f"    if nokta == {nokta!r}: os._exit(9)\n"
           "sg._fault = cok\n"
           f"sg.run_startup_gate_ileri({eski_db!r})\n"
           "os._exit(0)\n")
    sonuc = _cocuk(kod)
    assert sonuc.returncode == 9, sonuc.stderr
    assert os.path.exists(eski_db + sg.ILERI_JOURNAL_SUFFIX), "çökme günlük bırakmalı"

    rapor = sg.run_startup_gate_ileri(eski_db)
    beklenen = {"ileri_gunluk_yazildi": "YAYIM_BASLAMADI", "ileri_yedek_kopyalandi": "YAYIM_BASLAMADI",
                "ileri_yayim_gunluk_oncesi": "YAYIM_TAMAMLANDI", "ileri_yedek_silme_oncesi": "YAYIM_TAMAMLANDI"}
    assert rapor.details["yarim_kalan_yayim"] == beklenen[nokta]
    assert rapor.terminal_revision == sg.APPLICATION_HEAD
    sonra = _tum_tablo_icerikleri(eski_db, haric=("alembic_version",))
    assert {t: sonra[t] for t in once} == once
    _kalinti_yok(eski_db)


def _yayimlanmis_ve_yeni_veri_yazilmis(eski_db, eski_ana):
    """Başarılı geçiş + uygulama yeni satır yazdı + (çökme artığı) eski yedek ve YAYIMLANDI günlüğü."""
    kaynak = _sha256(eski_ana)
    sg.run_startup_gate_ileri(eski_db)
    yeni = _sha256(eski_db)
    shutil.copyfile(eski_ana, eski_db + sg.ILERI_PREPUBLISH_SUFFIX)
    sg._gunluk_yaz(eski_db, {"asama": "YAYIMLANDI", "kaynak_sha256": kaynak, "yeni_sha256": yeni})
    con = sqlite3.connect(eski_db)
    con.execute("INSERT INTO offers (consumption_kwh, current_unit_price, weighted_ptf, yekdem, agreement_multiplier, "
                "current_total, offer_total, savings_amount, savings_ratio, created_at) "
                "VALUES (777,1,1,1,1,1,1,1,0,'2099-03-01 00:00:00')")
    con.commit()
    con.close()


def test_kurtarma_gecis_sonrasi_yeni_veriyi_eski_kopyayla_ezmez(eski_db, eski_sema_ana):
    _yayimlanmis_ve_yeni_veri_yazilmis(eski_db, eski_sema_ana)
    rapor = sg.run_startup_gate_ileri(eski_db)
    assert rapor.details["yarim_kalan_yayim"] == "YAYIM_TAMAMLANDI"
    con = sqlite3.connect(eski_db)
    try:
        assert con.execute("SELECT COUNT(*) FROM offers WHERE consumption_kwh = 777").fetchone()[0] == 1
    finally:
        con.close()
    assert _revizyonlar(eski_db) == (sg.APPLICATION_HEAD,)
    _kalinti_yok(eski_db)


def test_yayimlanmis_db_kayipsa_eski_kopya_geri_konmaz_bos_db_kurulmaz(eski_db, eski_sema_ana):
    _yayimlanmis_ve_yeni_veri_yazilmis(eski_db, eski_sema_ana)
    os.replace(eski_db, eski_db + ".elle-tasindi")
    pre_ozet = _sha256(eski_db + sg.ILERI_PREPUBLISH_SUFFIX)
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_RECOVERY_AMBIGUOUS
    assert not os.path.exists(eski_db), "boş DB kurulmamalı / eski kopya geri konmamalı"
    assert _sha256(eski_db + sg.ILERI_PREPUBLISH_SUFFIX) == pre_ozet
    assert os.path.exists(eski_db + sg.ILERI_JOURNAL_SUFFIX)


@pytest.mark.parametrize("bozulma", ["gunluksuz_yedek", "baska_db_gunlugu", "yedek_ozeti_farkli", "icerik_ozeti_bozuk"])
def test_kimlige_baglanamayan_kurtarma_dosyalara_dokunmaz(eski_db, tmp_path, bozulma):
    kaynak = _sha256(eski_db)
    prepublish = eski_db + sg.ILERI_PREPUBLISH_SUFFIX
    shutil.copyfile(eski_db, prepublish)  # canonical yerinde; yalnız yedek + günlük durumu bozulur
    if bozulma == "baska_db_gunlugu":
        baska = str(tmp_path / "baska" / "gelka_enerji.db")
        os.makedirs(os.path.dirname(baska))
        sg._gunluk_yaz(baska, {"asama": "YEDEKLENIYOR", "kaynak_sha256": kaynak, "yeni_sha256": "0" * 64})
        shutil.copyfile(sg._gunluk_yolu(baska), sg._gunluk_yolu(eski_db))
    elif bozulma == "yedek_ozeti_farkli":
        sg._gunluk_yaz(eski_db, {"asama": "YAYIMLANDI", "kaynak_sha256": "f" * 64, "yeni_sha256": "0" * 64})
    elif bozulma == "icerik_ozeti_bozuk":
        sg._gunluk_yaz(eski_db, {"asama": "YEDEKLENIYOR", "kaynak_sha256": kaynak, "yeni_sha256": "0" * 64})
        with open(sg._gunluk_yolu(eski_db), encoding="utf-8") as fh:
            paket = json.load(fh)
        paket["gunluk"]["asama"] = "YAYIMLANDI"  # özet güncellenmeden değiştirildi
        with open(sg._gunluk_yolu(eski_db), "w", encoding="utf-8") as fh:
            json.dump(paket, fh)
    durum_once = {ad: _sha256(os.path.join(os.path.dirname(eski_db), ad))
                  for ad in os.listdir(os.path.dirname(eski_db)) if not ad.endswith(sg.ILERI_LOCK_SUFFIX)}
    with pytest.raises(sg.GateRefused) as exc:
        sg.run_startup_gate_ileri(eski_db)
    assert exc.value.exit_code == sg.EXIT_RECOVERY_AMBIGUOUS
    durum_sonra = {ad: _sha256(os.path.join(os.path.dirname(eski_db), ad))
                   for ad in os.listdir(os.path.dirname(eski_db)) if not ad.endswith(sg.ILERI_LOCK_SUFFIX)}
    assert durum_sonra == durum_once
