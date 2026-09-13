"""
PDSMR-R3 — baslangic sema kapisi + kontrollu adoption baglama.

app.main import edilmeden, hicbir ORM/router yan etkisi, init_db(), create_all(),
seed rutini veya port bind ISLEMEDEN ONCE calisir (bkz. run_server.py,
STEP 3). Bu modul BILEREK app.database'i (ya da onu tetikleyecek app.main'i)
DOGRUDAN import ETMEZ — yalniz sqlite3 + alembic_runner (alt surec) kullanir,
tipki app/legacy_adoption paketinin geri kalani gibi (rescue.py/adoption.py/
validator.py AYNI ilke).

Tipik, DETERMINISTIK durum makinesi (owner STEP 1):
  A) DB_ABSENT                        -> fresh_initialize()
  B) VERIFIED_LEGACY_013               -> perform_controlled_adoption()
  C) VERIFIED_CANONICAL_HEAD           -> certify_canonical() (hafif no-op)
  D) PARTIAL_S5_SCHEMA                 -> HARD_STOP (create_all fail-open izi)
  E) UNKNOWN_SCHEMA                    -> HARD_STOP
  F) INTERRUPTED_ADOPTION_STATE        -> deterministik kurtarma (kalinti
                                           temizle, canonical'dan yeniden
                                           baslat — canonical KENDISI hicbir
                                           zaman yerinde MUTASYONA UGRAMAZ)
  G) CANONICAL_ABSENT_LEGACY_EXISTS    -> HARD_STOP (rescue atlanmis/basarisiz)

  H) VERIFIED_APPLICATION_HEAD         -> certify_application_head() (hafif no-op)

ILERI MIGRATION (fiyat onay revizyonlari, b7e4c2d91a60):
  run_startup_gate_ileri(): run_startup_gate() canonical basa (351d314819d5) ulasirsa
  ileri_migration_uygula() canonical'i CALISMA KOPYASINDA uygulama basina tasir, eski
  tablolarin icerik ozetlerini once/sonra karsilastirir, sertifikalar ve ATOMIK yayimlar.
  Hata/kesintide canonical DEGISMEZ (GateRefused 54); yarim kalan yayim bir sonraki
  acilista DB kimligine bagli, icerik ozeti denetimli (imza DEGIL) gunlukle toparlanir
  (belirsizse 56, dosyaya dokunulmaz). Kopyadan degistirmeye kadar canonical Windows'ta
  YAZMAYI REDDEDEN tutamak altindadir; degistirme POSIX anlamli atomiktir. Surecler arasi
  acilis kilidi, SQLite yan dosyasi ve acik yazici denetimleri: 55.

Hicbir dal varsayilan/bilinmeyen olarak app baslangicina GECEMEZ.

YASAKLAR (kod duzeyinde zorlanir):
  - create_all() ASLA cagrilmaz (bu modul Base.metadata'yi hic bilmez)
  - canonical dosyasi YERINDE mutasyona UGRATILMAZ (working kopya + atomik
    yayimlama — rescue.py/adoption.py ile AYNI ilke)
  - alembic calistirilamiyorsa (frozen'da companion exe yoksa) SESSIZCE
    create_all'a DUSULMEZ — HARD_STOP

Cagrildigi yerler:
- backend/run_server.py::main() [PDSMR-R3, app.main import'undan ONCE]
- tests/test_pdsmr_r3_startup_gate.py
"""
from __future__ import annotations

import contextlib
import hashlib
import json
import os
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from . import alembic_runner as ar
from . import policy
from .adoption import (
    AdoptionRefused,
    adopt_legacy_copy,
    canonical_yapisi_saglam,
    is_certifiably_canonical,
)
from .adoption import InjectedFault as _AdoptionInjectedFault
from .lineage import CANONICAL_HEAD, PRODUCTION_BRANCH_TIP
from .pathsafety import is_forbidden_target, real_path, same_file
from .rescue import read_journal
from .result import Outcome as ValidatorOutcome
from .validator import validate_legacy_db

AUDIT_SUFFIX = ".pdsmr-r3-bootstrap-audit.json"
AUDIT_VERSION = "PDSMR-R3/1"

WORKING_SUFFIX = ".pdsmr-r3-working"
SOURCE_SUFFIX = ".pdsmr-r3-source"
FRESH_SUFFIX = ".pdsmr-r3-fresh"

# ── Kategorize exit code semasi (rescue.py ile AYNI numaralandirma
# ailesi — 10-39 rescue.py'nin, 40-59 bu modulun kendi kategorileridir,
# cakisma olmasin diye) ────────────────────────────────────────────────
EXIT_OK = 0
EXIT_HARD_STOP_PARTIAL_SCHEMA = 40           # D
EXIT_HARD_STOP_UNKNOWN_SCHEMA = 41           # E
EXIT_HARD_STOP_LEGACY_VALIDATION_FAILED = 42  # B alt-dali: validate_legacy_db HARD_STOP
EXIT_HARD_STOP_CANONICAL_ABSENT_LEGACY_EXISTS = 43  # G
EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID = 44  # B alt-dali: R2 rescue journal yok/bozuk
EXIT_FRESH_INIT_FAILED = 50                  # A basarisiz (alembic dahil)
EXIT_ADOPTION_FAILED = 51                    # B basarisiz (adopt_legacy_copy reddetti)
EXIT_ALEMBIC_UNAVAILABLE = 52                # A/B: frozen'da alembic yok — create_all'a DUSULMEZ
EXIT_CERTIFICATION_FAILED = 53               # C: canonical revizyonu doğru ama sema tutmuyor
EXIT_FORWARD_MIGRATION_FAILED = 54           # ileri migration basarisiz (canonical DEGISMEDI)
EXIT_CONCURRENT_OR_BUSY = 55                 # ikinci acilis / DB baska baglantida / SQLite yan dosyasi
EXIT_RECOVERY_AMBIGUOUS = 56                 # yarim yayim kimlige baglanamadi — dosyalara dokunulmadi

# ── Uygulama basi: canonical + katkisal ileri migration(lar) ─────────────
# Canonical (PDSMR) bas 351d314819d5 DEGISMEDI; uygulama basi onun uzerine YALNIZ yeni
# tablo ekleyen revizyondur. Beklenen kolon kumeleri app/price_approval.onay_metadata ile
# AYNIDIR (tests/test_startup_gate_ileri_migration.py dogrular); bu modul app.database'i
# import ETMEZ, bu yuzden kume burada sabittir.
APPLICATION_HEAD = "b7e4c2d91a60"
APPLICATION_HEAD_TABLE_COLUMNS = {
    "ptf_onay_revizyonlari": frozenset({
        "id", "period", "revision", "price_record_id", "value", "basis", "kaynak_kanit_sha256",
        "kaynak_kanit_json", "kayit_parmak_izi", "captured_at", "onaylayan_beyan", "dogrulanan_yetki",
        "approved_at", "change_reason"}),
    "yekdem_onay_revizyonlari": frozenset({
        "id", "period", "segment", "revision", "value", "version", "kaynak_kanit_sha256",
        "kaynak_kanit_json", "captured_at", "onaylayan_beyan", "dogrulanan_yetki", "approved_at",
        "change_reason"}),
}
ILERI_WORKING_SUFFIX = ".pdsmr-ileri-working"
ILERI_PREPUBLISH_SUFFIX = ".pdsmr-ileri-prepublish"
ILERI_JOURNAL_SUFFIX = ".pdsmr-ileri-yayim-gunlugu.json"
ILERI_LOCK_SUFFIX = ".pdsmr-acilis-kilidi"
ILERI_FAULT_POINTS = (
    "ileri_calisma_kopyasi_oncesi",
    "ileri_migration_sonrasi",
    "ileri_yayim_oncesi",
    "ileri_son_sha_sonrasi",
    "ileri_gunluk_yazildi",
    "ileri_yedek_kopyalandi",
    "ileri_yayim_gunluk_oncesi",
    "ileri_yedek_silme_oncesi",
    "ileri_yayim_sonrasi",
)
# Bu noktalardan sonra yeni dosya YAYIMLANMISTIR (canonical = uygulama basi).
ILERI_YAYIM_SONRASI_NOKTALAR = ("ileri_yayim_gunluk_oncesi", "ileri_yedek_silme_oncesi", "ileri_yayim_sonrasi")
EXIT_PRECONDITION = 20
EXIT_FILESYSTEM = 30
EXIT_UNEXPECTED = 99


class BootstrapState(str, Enum):
    DB_ABSENT = "DB_ABSENT"
    VERIFIED_LEGACY_013 = "VERIFIED_LEGACY_013"
    VERIFIED_CANONICAL_HEAD = "VERIFIED_CANONICAL_HEAD"
    PARTIAL_S5_SCHEMA = "PARTIAL_S5_SCHEMA"
    UNKNOWN_SCHEMA = "UNKNOWN_SCHEMA"
    CANONICAL_ABSENT_LEGACY_EXISTS = "CANONICAL_ABSENT_LEGACY_EXISTS"
    VERIFIED_APPLICATION_HEAD = "VERIFIED_APPLICATION_HEAD"


class GateRefused(Exception):
    """Kapida HARD_STOP. Canonical dosyasina ANLAMLI YAZMA YAPILMADI."""

    def __init__(self, message: str, exit_code: int = EXIT_PRECONDITION,
                 state: Optional[BootstrapState] = None):
        super().__init__(message)
        self.exit_code = exit_code
        self.state = state


class InjectedFault(Exception):
    """Test amacli kesinti. Gercek bir arizayi taklit eder."""


# STEP 6 — fault noktalari. Ilk 5'i adoption.py::FAULT_POINTS ile BIREBIR
# AYNI isimdir (dogrudan pass-through edilir); kalanlar bu modulun KENDI
# (working-copy olusturma / atomik yayimlama / audit) noktalaridir.
GATE_FAULT_POINTS = (
    "before_working_copy_creation",
    "during_working_copy_creation",
    "before_repair",
    "after_repair",
    "before_lineage_reconciliation",
    "after_lineage_reconciliation",
    "during_forward_migration",
    "before_atomic_publish",
    "after_atomic_publish",
    "before_audit_finalize",
    "after_audit_finalize",
    "before_alembic_run",       # fresh-init'e ozel
    "after_alembic_run",        # fresh-init'e ozel
)
_ADOPT_FAULT_RENAME = {
    "before_repair": "before_repair",
    "after_repair": "after_repair",
    "before_lineage_reconciliation": "before_lineage",
    "after_lineage_reconciliation": "after_lineage",
    "during_forward_migration": "during_forward",
}


def _fault(point: str, fault_at: Optional[str]) -> None:
    if fault_at == point:
        raise InjectedFault(f"enjekte edilmis kesinti: {point}")


@dataclass
class GateResult:
    state: BootstrapState
    action: str  # "NONE" | "FRESH_INITIALIZED" | "ADOPTED" | "CERTIFIED_NOOP"
    terminal_revision: str = ""
    heads: int = 0
    integrity_check: str = ""
    foreign_key_violations: int = -1
    row_counts: dict[str, int] = field(default_factory=dict)
    details: dict[str, Any] = field(default_factory=dict)


# ── Salt-okunur sonda yardimcilari (adoption.py/rescue.py ile AYNI ilke) ──
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def _is_valid_sqlite(path: str) -> bool:
    try:
        con = sqlite3.connect(_ro(path), uri=True)
        try:
            con.execute("PRAGMA schema_version").fetchone()
            return True
        finally:
            con.close()
    except sqlite3.DatabaseError:
        return False


def _revisions(path: str) -> tuple[str, ...]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return tuple(sorted(
            r[0] for r in con.execute(
                "SELECT version_num FROM alembic_version"
            ).fetchall()
        ))
    except sqlite3.OperationalError:
        return ()
    finally:
        con.close()


def _tables(path: str) -> set[str]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' "
                "AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        con.close()


def _health(path: str) -> tuple[str, int]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return (
            con.execute("PRAGMA integrity_check").fetchone()[0],
            len(con.execute("PRAGMA foreign_key_check").fetchall()),
        )
    finally:
        con.close()


def _row_counts(path: str) -> dict[str, int]:
    tablolar = _tables(path)
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            # PDSMR-R4/Faz3: EXPECTED_ROW_COUNTS (deger tasiyan, kabul/red
            # politikasi) kaldirildi. ROW_COUNT_REPORT_TABLES ayni tablo
            # adlarini deger TASIMADAN tutar — davranis birebir aynidir.
            for t in sorted(policy.ROW_COUNT_REPORT_TABLES) if t in tablolar
        }
    finally:
        con.close()


def _fsync_close(path: str) -> None:
    """
    Onceden KAPATILMIS bir dosyanin (baska bir sqlite3 baglantisi tarafindan
    yazilmis) hala OS yazma tamponunda olabilecek verisini diske ZORLAR.

    DIKKAT: Windows'ta salt-okunur (O_RDONLY) acilmis bir fd uzerinde fsync
    "Bad file descriptor" ile BASARISIZ olur - fsync yazma icin acilmis bir
    fd GEREKTIRIR (rescue.py::_copy_bytes_durable'in kendi yazma sirasinda
    fsync yapmasiyla AYNI ilke, burada dosya ZATEN yazilmis/kapatilmis
    oldugundan O_RDWR ile YENIDEN acilir).
    """
    fd = os.open(path, os.O_RDWR)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _working_paths(canonical_path: str) -> dict[str, str]:
    return {
        "working": canonical_path + WORKING_SUFFIX,
        "source": canonical_path + SOURCE_SUFFIX,
        "fresh": canonical_path + FRESH_SUFFIX,
    }


def _cleanup_stale_artifacts(canonical_path: str) -> list[str]:
    """
    ONCEKI CRASH'TEN kalan calisma/gecici dosyalarini temizler.

    Bu, "INTERRUPTED_ADOPTION_STATE"nin deterministik kurtarma stratejisidir:
    canonical dosyasi KENDISI HICBIR ZAMAN yerinde mutasyona UGRAMADIGINDAN
    (yalniz working kopya + atomik rename), kalinti bir working/source/fresh
    dosyasi GUVENLE silinip is bastan alinabilir — canonical'in durumu
    (013 / head / yok) BUNDAN ETKILENMEZ.

    Cagrildigi yerler:
    - classify_startup_state() [her calistirmada, siniflandirmadan ONCE]
    """
    silinen = []
    for yol in _working_paths(canonical_path).values():
        if os.path.isfile(yol):
            try:
                os.remove(yol)
                silinen.append(yol)
            except OSError:
                pass
    return silinen


# ── STEP 1: siniflandirma ────────────────────────────────────────────────
def classify_startup_state(
    canonical_path: str, *, legacy_hint_path: Optional[str] = None
) -> BootstrapState:
    """
    SALT-OKUNUR, YAN ETKISIZ (kalinti temizligi HARIC — bkz. yukarida).

    Args:
        canonical_path: `<userData>/database/gelka_enerji.db`.
        legacy_hint_path: `resources/backend/gelka_enerji.db` — YALNIZ
            G durumunu (rescue atlanmis) tespit etmek icin okunur, ASLA
            yazilmaz/silinmez (bu modulun sorumlulugu DEGIL — PDSMR-R2
            rescue.exe'nin isidir).

    Cagrildigi yerler:
    - run_startup_gate() [PDSMR-R3]
    - tests/test_pdsmr_r3_startup_gate.py
    """
    _cleanup_stale_artifacts(canonical_path)

    if not os.path.isfile(canonical_path):
        if legacy_hint_path and os.path.isfile(legacy_hint_path):
            return BootstrapState.CANONICAL_ABSENT_LEGACY_EXISTS
        return BootstrapState.DB_ABSENT

    if not _is_valid_sqlite(canonical_path):
        return BootstrapState.UNKNOWN_SCHEMA

    revizyonlar = _revisions(canonical_path)
    if len(revizyonlar) != 1:
        return BootstrapState.UNKNOWN_SCHEMA

    rev = revizyonlar[0]

    if rev == CANONICAL_HEAD:
        if is_certifiably_canonical(canonical_path):
            return BootstrapState.VERIFIED_CANONICAL_HEAD
        return BootstrapState.UNKNOWN_SCHEMA

    if rev == APPLICATION_HEAD:
        if is_certifiably_application_head(canonical_path):
            return BootstrapState.VERIFIED_APPLICATION_HEAD
        return BootstrapState.UNKNOWN_SCHEMA

    if rev == PRODUCTION_BRANCH_TIP:
        s5_var = policy.EXPECTED_ABSENT_MODEL_TABLES & _tables(canonical_path)
        if s5_var:
            # create_all() fail-open imzasi: 013'te ama S5 tablolari VAR.
            return BootstrapState.PARTIAL_S5_SCHEMA
        return BootstrapState.VERIFIED_LEGACY_013

    return BootstrapState.UNKNOWN_SCHEMA


# ── STEP 4: taze kurulum ─────────────────────────────────────────────────
def fresh_initialize(canonical_path: str, *, fault_at: Optional[str] = None) -> GateResult:
    """
    DB_ABSENT icin: gecici bir DB'de Alembic base->head calistirir, dogrular,
    ATOMIK olarak canonical konuma yayimlar. create_all() KULLANMAZ.

    Frozen paketlemede alembic calistirilamiyorsa (companion exe yok):
    AlembicUnavailable -> GateRefused(EXIT_ALEMBIC_UNAVAILABLE). create_all'a
    ASLA DUSULMEZ (owner karari, PDSMR-R3 STEP 4/7).
    """
    if os.path.exists(canonical_path):
        raise GateRefused(
            "canonical zaten mevcut — fresh_initialize yalniz DB_ABSENT icin",
            EXIT_PRECONDITION,
        )
    if is_forbidden_target(canonical_path):
        raise GateRefused(
            "hedef kurulu uygulama alaninda", EXIT_PRECONDITION
        )

    fresh = _working_paths(canonical_path)["fresh"]
    ust_dizin = os.path.dirname(real_path(canonical_path))
    if not os.path.isdir(ust_dizin):
        os.makedirs(ust_dizin, exist_ok=True)

    if os.path.exists(fresh):
        try:
            os.remove(fresh)
        except OSError as exc:
            raise GateRefused(f"kalinti fresh dosyasi silinemedi: {exc}", EXIT_FILESYSTEM) from exc

    try:
        if not ar.is_alembic_available():
            raise GateRefused(
                "alembic calistirilabiliri yok (frozen companion-exe eksik) — "
                "create_all'a DUSULMEDI, HARD_STOP",
                EXIT_ALEMBIC_UNAVAILABLE,
            )

        _fault("before_alembic_run", fault_at)
        try:
            ar.alembic_upgrade(fresh, CANONICAL_HEAD)
        except RuntimeError as exc:
            raise GateRefused(f"alembic base->head basarisiz: {exc}", EXIT_FRESH_INIT_FAILED) from exc
        _fault("after_alembic_run", fault_at)

        revizyonlar = _revisions(fresh)
        if revizyonlar != (CANONICAL_HEAD,):
            raise GateRefused(
                f"taze DB terminal revizyonu {CANONICAL_HEAD} degil: {revizyonlar}",
                EXIT_FRESH_INIT_FAILED,
            )
        if not is_certifiably_canonical(fresh):
            raise GateRefused(
                "taze DB revizyonu dogru ama sema sertifikasyonu basarisiz",
                EXIT_FRESH_INIT_FAILED,
            )
        butunluk, fk = _health(fresh)
        if butunluk != "ok" or fk:
            raise GateRefused(
                f"taze DB dogrulamasi basarisiz: integrity={butunluk} fk={fk}",
                EXIT_FRESH_INIT_FAILED,
            )

        _fsync_close(fresh)

        _fault("before_atomic_publish", fault_at)
        if os.path.exists(canonical_path):
            raise GateRefused(
                "canonical hedef bekleme SIRASINDA olustu — cakisma, rename yapilmadi",
                EXIT_FILESYSTEM,
            )
        os.rename(fresh, canonical_path)
        _fault("after_atomic_publish", fault_at)

        heads = ar.alembic_heads_count(canonical_path)
        rapor = GateResult(
            state=BootstrapState.DB_ABSENT,
            action="FRESH_INITIALIZED",
            terminal_revision=CANONICAL_HEAD,
            heads=heads,
            integrity_check=butunluk,
            foreign_key_violations=fk,
            row_counts=_row_counts(canonical_path),
        )
        _fault("before_audit_finalize", fault_at)
        _write_bootstrap_audit(canonical_path, {
            "action": rapor.action,
            "terminal_revision": rapor.terminal_revision,
            "heads": rapor.heads,
            "integrity_check": rapor.integrity_check,
            "foreign_key_violations": rapor.foreign_key_violations,
        })
        _fault("after_audit_finalize", fault_at)
        return rapor
    except InjectedFault:
        raise
    except GateRefused:
        raise
    except OSError as exc:
        raise GateRefused(f"dosya sistemi hatasi: {type(exc).__name__}: {exc}", EXIT_FILESYSTEM) from exc
    finally:
        if os.path.exists(fresh):
            try:
                os.remove(fresh)
            except OSError:
                pass


# ── STEP 5: kontrollu legacy adoption ────────────────────────────────────
def perform_controlled_adoption(
    canonical_path: str, *, fault_at: Optional[str] = None
) -> GateResult:
    """
    VERIFIED_LEGACY_013 icin: R2 rescue audit'ini + rollback yedegini
    dogrular, AYRI bir calisma kopyasinda PDSMR-R1D adopt_legacy_copy()'yi
    calistirir, tam sertifikasyondan SONRA ATOMIK olarak canonical'e yayimlar.

    canonical_path'in KENDISI hicbir asamada yerinde MUTASYONA UGRAMAZ —
    adoption BASARISIZ olursa canonical, girisdeki (013) haliyle DEGISMEDEN
    kalir (owner karari: "do not adopt the live canonical file in place").
    """
    yollar = _working_paths(canonical_path)
    working, source = yollar["working"], yollar["source"]

    journal = read_journal(canonical_path)
    if journal is None:
        raise GateRefused(
            "R2 rescue journal yok/imza dogrulamasi basarisiz — adoption "
            "GUVENLI ONKOSUL olmadan calistirilamaz",
            EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID,
        )
    rollback = journal.get("backup_path", "")
    kaynak_hash_beklenen = journal.get("source_sha256", "")
    if not rollback or not os.path.isfile(rollback):
        raise GateRefused(
            "R2 rollback yedegi eksik/okunamiyor", EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID
        )
    if not kaynak_hash_beklenen:
        raise GateRefused(
            "R2 rescue journal source_sha256 icermiyor",
            EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID,
        )
    if _sha256(rollback) != kaynak_hash_beklenen:
        raise GateRefused(
            "R2 rollback yedegi journal ile eslesmiyor — bozulmus/degistirilmis",
            EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID,
        )
    if _sha256(canonical_path) != kaynak_hash_beklenen:
        raise GateRefused(
            "canonical (013) dosyasi R2 rescue'dan BERI DEGISMIS — beklenmedik "
            "mutasyon, adoption reddedildi",
            EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID,
        )

    # Tam yapisal on-kosul: PDSMR-R1D Faz2 validator'i (allowlist revizyon,
    # row-count parity, kolon/index semasi) — kod tekrari yok, DOGRUDAN cagrilir.
    dogrulama = validate_legacy_db(canonical_path)
    if dogrulama.outcome != ValidatorOutcome.PASS:
        raise GateRefused(
            "validate_legacy_db HARD_STOP: "
            + "; ".join(f"{f.reason_code}" for f in dogrulama.findings[:5]),
            EXIT_HARD_STOP_LEGACY_VALIDATION_FAILED,
        )

    for yol in (working, source):
        if os.path.exists(yol):
            try:
                os.remove(yol)
            except OSError as exc:
                raise GateRefused(f"kalinti dosya silinemedi: {exc}", EXIT_FILESYSTEM) from exc

    try:
        _fault("before_working_copy_creation", fault_at)
        _copy_bytes_durable(canonical_path, source)
        _fault("during_working_copy_creation", fault_at)
        _copy_bytes_durable(canonical_path, working)

        if _sha256(source) != kaynak_hash_beklenen or _sha256(working) != kaynak_hash_beklenen:
            raise GateRefused(
                "calisma/kaynak kopyasi parmak izi eslesmiyor — kopyalama basarisiz",
                EXIT_FILESYSTEM,
            )

        adopt_kwargs = {}
        if fault_at in _ADOPT_FAULT_RENAME:
            adopt_kwargs["fault_at"] = _ADOPT_FAULT_RENAME[fault_at]

        try:
            adoption_rapor = adopt_legacy_copy(
                working,
                source_path=source,
                rollback_path=rollback,
                expected_source_sha256=kaynak_hash_beklenen,
                confirm_disposable_copy=True,
                **adopt_kwargs,
            )
        except AdoptionRefused as exc:
            raise GateRefused(f"adoption reddedildi: {exc}", EXIT_ADOPTION_FAILED) from exc
        except _AdoptionInjectedFault as exc:
            # adoption.py'nin KENDI InjectedFault'u (5 ic fault noktasindan
            # pass-through) - bu modulun TEK, tutarli InjectedFault yuzeyine
            # cevrilir ki cagiran YALNIZ startup_gate.InjectedFault'u
            # yakalamasi YETSIN (adoption.py'nin ic siniflarini bilmesi
            # GEREKMESIN).
            raise InjectedFault(str(exc)) from exc

        if adoption_rapor.outcome == "ADOPTED":
            _fsync_close(working)
            _fault("before_atomic_publish", fault_at)
            if _sha256(canonical_path) != kaynak_hash_beklenen:
                raise GateRefused(
                    "canonical adoption SIRASINDA DEGISTI — rename yapilmadi",
                    EXIT_FILESYSTEM,
                )
            _yedek = canonical_path + ".pdsmr-r3-prepublish"
            os.replace(canonical_path, _yedek)
            try:
                os.rename(working, canonical_path)
            except OSError:
                os.replace(_yedek, canonical_path)  # geri al
                raise
            os.remove(_yedek)
            _fault("after_atomic_publish", fault_at)
        # ALREADY_ADOPTED: working zaten canonical'in bir kopyasiydi ve
        # zaten head'de bulundu — canonical'e DOKUNULMADAN NOOP.

        rapor = GateResult(
            state=BootstrapState.VERIFIED_LEGACY_013,
            action="ADOPTED" if adoption_rapor.outcome == "ADOPTED" else "CERTIFIED_NOOP",
            terminal_revision=adoption_rapor.terminal_revision,
            heads=adoption_rapor.heads,
            integrity_check=adoption_rapor.integrity_check or "ok",
            foreign_key_violations=max(adoption_rapor.foreign_key_violations, 0),
            row_counts=adoption_rapor.row_counts,
            details={"adoption_outcome": adoption_rapor.outcome},
        )
        _fault("before_audit_finalize", fault_at)
        _write_bootstrap_audit(canonical_path, {
            "action": rapor.action,
            "terminal_revision": rapor.terminal_revision,
            "heads": rapor.heads,
            "adoption_outcome": adoption_rapor.outcome,
            "source_sha256": kaynak_hash_beklenen,
        })
        _fault("after_audit_finalize", fault_at)
        return rapor
    except InjectedFault:
        raise
    except GateRefused:
        raise
    except OSError as exc:
        raise GateRefused(f"dosya sistemi hatasi: {type(exc).__name__}: {exc}", EXIT_FILESYSTEM) from exc
    finally:
        for yol in (working, source):
            if os.path.exists(yol):
                try:
                    os.remove(yol)
                except OSError:
                    pass


def _copy_bytes_durable(src: str, dst: str) -> None:
    """Duz byte kopyasi + fsync — rescue.py::_copy_bytes_durable ile AYNI ilke
    (SQLite Online Backup API BILEREK kullanilmaz, bkz. rescue.py yorumu)."""
    with open(src, "rb") as kaynak, open(dst, "wb") as hedef:
        while True:
            parca = kaynak.read(1 << 20)
            if not parca:
                break
            hedef.write(parca)
        hedef.flush()
        os.fsync(hedef.fileno())


# ── STEP C: zaten canonical — hafif sertifikasyon ────────────────────────
def certify_canonical(canonical_path: str) -> GateResult:
    """VERIFIED_CANONICAL_HEAD icin: HICBIR DDL mutasyonu yapmaz, yalniz
    tekrar dogrular (owner: "Canonical head may proceed without DDL
    mutation")."""
    if not is_certifiably_canonical(canonical_path):
        raise GateRefused(
            "canonical DB artik sertifikalanamiyor (harici mutasyon?)",
            EXIT_CERTIFICATION_FAILED,
        )
    butunluk, fk = _health(canonical_path)
    if butunluk != "ok" or fk:
        raise GateRefused(
            f"canonical dogrulamasi basarisiz: integrity={butunluk} fk={fk}",
            EXIT_CERTIFICATION_FAILED,
        )
    return GateResult(
        state=BootstrapState.VERIFIED_CANONICAL_HEAD,
        action="CERTIFIED_NOOP",
        terminal_revision=CANONICAL_HEAD,
        heads=ar.alembic_heads_count(canonical_path) if ar.is_alembic_available() else 1,
        integrity_check=butunluk,
        foreign_key_violations=fk,
        row_counts=_row_counts(canonical_path),
    )


# ── STEP H: uygulama basi — sertifikasyon + kontrollu ileri migration ────
def is_certifiably_application_head(path: str) -> bool:
    """Uygulama basi (APPLICATION_HEAD) kaniti: tek revizyon + canonical yapisi + onay tablolari.

    Cagrildigi yerler:
    - classify_startup_state() [H durumu]
    - certify_application_head(), ileri_migration_uygula() (yayim oncesi dogrulama)
    """
    if _revisions(path) != (APPLICATION_HEAD,):
        return False
    if not canonical_yapisi_saglam(path):
        return False
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        for tablo, kolonlar in APPLICATION_HEAD_TABLE_COLUMNS.items():
            gercek = {r[1] for r in con.execute(f"PRAGMA table_info({tablo})").fetchall()}
            if gercek != set(kolonlar):
                return False
    finally:
        con.close()
    return True


def certify_application_head(canonical_path: str) -> GateResult:
    """VERIFIED_APPLICATION_HEAD icin: DDL mutasyonu YOK, yalniz yeniden dogrulama."""
    if not is_certifiably_application_head(canonical_path):
        raise GateRefused("uygulama basi DB artik sertifikalanamiyor (harici mutasyon?)",
                          EXIT_CERTIFICATION_FAILED)
    butunluk, fk = _health(canonical_path)
    if butunluk != "ok" or fk:
        raise GateRefused(f"uygulama basi dogrulamasi basarisiz: integrity={butunluk} fk={fk}",
                          EXIT_CERTIFICATION_FAILED)
    return GateResult(
        state=BootstrapState.VERIFIED_APPLICATION_HEAD,
        action="CERTIFIED_NOOP",
        terminal_revision=APPLICATION_HEAD,
        heads=ar.alembic_heads_count(canonical_path) if ar.is_alembic_available() else 1,
        integrity_check=butunluk,
        foreign_key_violations=fk,
        row_counts=_row_counts(canonical_path),
    )


def _tablo_icerik_ozetleri(path: str, tablolar: set[str]) -> dict[str, str]:
    """Verilen tablolarin TAM icerik ozeti (rowid sirali; salt-okunur). Satir sayisi yetmez."""
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        ozet = {}
        for t in sorted(tablolar):
            h = hashlib.sha256()
            for satir in con.execute(f'SELECT * FROM "{t}" ORDER BY rowid'):
                h.update(repr(tuple(satir)).encode("utf-8"))
                h.update(b"\n")
            ozet[t] = h.hexdigest()
        return ozet
    finally:
        con.close()


def _ileri_yollar(canonical_path: str) -> tuple[str, str]:
    return canonical_path + ILERI_WORKING_SUFFIX, canonical_path + ILERI_PREPUBLISH_SUFFIX


# ── Ileri migration guvenlik yardimcilari ────────────────────────────────
# Surecler arasi acilis kilidi: isletim sistemi kilidi (msvcrt/fcntl). Surec olunce kilit
# kendiliginden birakilir → bayat kilit dosyasi acilisi ENGELLEMEZ. Kilit dosyasi yerinde
# kalir (silmek POSIX'te karsilikli dislamayi bozar). Ayni surecte ic ice cagri serbesttir.
_TUTULAN_KILITLER: set[str] = set()


def _kimlik_yolu(path: str) -> str:
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


@contextlib.contextmanager
def acilis_kilidi(canonical_path: str):
    """Ayni canonical icin ayni anda TEK kapi calismasi (ikinci acilis 55 ile durur).

    Cagrildigi yerler:
    - run_startup_gate_ileri(), ileri_migration_uygula() [dogrudan cagrilirsa]
    """
    anahtar = _kimlik_yolu(canonical_path)
    if anahtar in _TUTULAN_KILITLER:
        yield
        return
    os.makedirs(os.path.dirname(os.path.abspath(canonical_path)), exist_ok=True)
    fd = os.open(canonical_path + ILERI_LOCK_SUFFIX, os.O_RDWR | os.O_CREAT, 0o600)
    kilitli = False
    try:
        try:
            if os.name == "nt":
                import msvcrt

                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
            else:
                import fcntl

                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
            kilitli = True
        except OSError as exc:
            raise GateRefused("ayni veritabani icin baska bir acilis/kapi calismasi suruyor — "
                              "ikinci acilis durduruldu", EXIT_CONCURRENT_OR_BUSY) from exc
        _TUTULAN_KILITLER.add(anahtar)
        try:
            yield
        finally:
            _TUTULAN_KILITLER.discard(anahtar)
    finally:
        if kilitli:
            try:
                if os.name == "nt":
                    import msvcrt

                    os.lseek(fd, 0, os.SEEK_SET)
                    msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
                else:
                    import fcntl

                    fcntl.flock(fd, fcntl.LOCK_UN)
            except OSError:
                pass
        os.close(fd)


def _sqlite_yan_dosya_yok(path: str) -> None:
    """-wal/-shm/-journal varsa DUZ BYTE kopyasi tutarsiz olabilir → dokunmadan dur.

    rescue.py ve production_adoption_controller.py ile AYNI ilke. Kontrol, dosyaya herhangi
    bir SQLite baglantisi acilmadan ONCE yapilir (sicak gunluk geri alinmasin, bayt degismesin).
    """
    for ek in ("-wal", "-shm", "-journal"):
        if os.path.exists(path + ek):
            raise GateRefused(f"SQLite yan dosyasi mevcut ({os.path.basename(path)}{ek}) — acik baglanti ya da "
                              "yarim islem olabilir; ileri migration yapilmadi", EXIT_CONCURRENT_OR_BUSY)


# ── Yayim yazma kilidi (Windows) ─────────────────────────────────────────
# SHA karsilastirmasi kilit DEGILDIR: son kontrol ile dosya degistirme arasinda baska bir yazici
# commit edebilir ve verisi eski dosyayla birlikte kaybolur (olculdu). Bu yuzden canonical,
# kopyadan degistirmeye kadar YAZMAYI REDDEDEN bir isletim sistemi tutamagi altindadir:
#   CreateFileW(GENERIC_READ, FILE_SHARE_READ | FILE_SHARE_DELETE)
# - Yazma erisimli acik baska bir tutamak varsa tutamak ALINAMAZ (ERROR_SHARING_VIOLATION) → 55.
# - Tutamak acikken baska surecler dosyayi yazma erisimiyle ACAMAZ (SQLite salt-okunura duser,
#   "attempt to write a readonly database"); okuma serbesttir.
# - Degistirme, POSIX anlamli atomik yeniden adlandirmadir (SetFileInformationByHandle /
#   FileRenameInfoEx, REPLACE_IF_EXISTS | POSIX_SEMANTICS): hedef yol hicbir an bos kalmaz.
#   Yeni dosyaya da yeniden adlandirmadan HEMEN once/sonra yazmayi reddeden tutamak alinir.
# Gereksinim: Windows 10 1709+ / NTFS. Desteklenmiyorsa degistirme yapilmaz (fail-closed).
_GENERIC_READ = 0x80000000
_DELETE = 0x00010000
_FILE_SHARE_READ = 0x1
_FILE_SHARE_DELETE = 0x4
_OPEN_EXISTING = 3
_FILE_ATTRIBUTE_NORMAL = 0x80
_ERROR_SHARING_VIOLATION = 32
_FILE_RENAME_INFO_EX = 22
_FILE_RENAME_FLAG_REPLACE_IF_EXISTS = 0x1
_FILE_RENAME_FLAG_POSIX_SEMANTICS = 0x2


def _k32():
    import ctypes
    from ctypes import wintypes

    k32 = ctypes.WinDLL("kernel32", use_last_error=True)
    k32.CreateFileW.restype = wintypes.HANDLE
    k32.CreateFileW.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, wintypes.DWORD, wintypes.LPVOID,
                                wintypes.DWORD, wintypes.DWORD, wintypes.HANDLE]
    k32.CloseHandle.argtypes = [wintypes.HANDLE]
    k32.CloseHandle.restype = wintypes.BOOL
    k32.SetFileInformationByHandle.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
    k32.SetFileInformationByHandle.restype = wintypes.BOOL
    return k32


def _yazmayi_reddeden_tutamak(path: str, *, silme_erisimi: bool = False) -> int:
    """Dosyaya yazmayi reddeden (okumaya ve atomik yeniden adlandirmaya izin veren) tutamak.

    Cagrildigi yerler:
    - _ileri_migration_uygula_kilitli() [canonical: kopya→degistirme; calisma kopyasi: degistirme]
    """
    import ctypes
    from ctypes import wintypes

    erisim = _GENERIC_READ | (_DELETE if silme_erisimi else 0)
    h = _k32().CreateFileW(os.path.abspath(path), erisim, _FILE_SHARE_READ | _FILE_SHARE_DELETE, None,
                           _OPEN_EXISTING, _FILE_ATTRIBUTE_NORMAL, None)
    if h is None or h == wintypes.HANDLE(-1).value:
        hata = ctypes.get_last_error()
        if hata == _ERROR_SHARING_VIOLATION:
            raise GateRefused(f"{os.path.basename(path)} baska bir surecte YAZMA erisimiyle acik — ileri "
                              "migration yapilmadi (uygulama/arac kapatilmali)", EXIT_CONCURRENT_OR_BUSY)
        raise GateRefused(f"yayim yazma kilidi alinamadi ({os.path.basename(path)}, winerror={hata})",
                          EXIT_FILESYSTEM)
    return h


def _tutamak_kapat(h: Optional[int]) -> None:
    if h is not None:
        _k32().CloseHandle(h)


def _posix_atomik_degistir(kaynak_tutamak: int, hedef: str) -> None:
    """Acik (DELETE erisimli) kaynak tutamagini hedef yolun uzerine atomik olarak adlandirir."""
    import ctypes
    import struct

    ad = os.path.abspath(hedef).encode("utf-16-le")
    tampon = ctypes.create_string_buffer(20 + len(ad) + 2)
    struct.pack_into("<I4xQI", tampon, 0,
                     _FILE_RENAME_FLAG_REPLACE_IF_EXISTS | _FILE_RENAME_FLAG_POSIX_SEMANTICS, 0, len(ad))
    ctypes.memmove(ctypes.addressof(tampon) + 20, ad, len(ad))
    if not _k32().SetFileInformationByHandle(kaynak_tutamak, _FILE_RENAME_INFO_EX, tampon, len(tampon)):
        hata = ctypes.get_last_error()
        raise GateRefused(f"atomik degistirme basarisiz (winerror={hata}) — canonical DEGISMEDI; "
                          "hedefte acik baska bir tutamak olabilir", EXIT_CONCURRENT_OR_BUSY
                          if hata == _ERROR_SHARING_VIOLATION else EXIT_FILESYSTEM)


def _ayni_birimde(a: str, b: str) -> bool:
    """Atomik rename icin iki yol AYNI klasor ve AYNI birim (st_dev) uzerinde mi?"""
    if os.path.dirname(os.path.abspath(a)) != os.path.dirname(os.path.abspath(b)):
        return False
    return os.stat(a).st_dev == os.stat(os.path.dirname(os.path.abspath(b)) or ".").st_dev


def _gunluk_yolu(canonical_path: str) -> str:
    return canonical_path + ILERI_JOURNAL_SUFFIX


def _gunluk_yaz(canonical_path: str, veri: dict) -> None:
    """Yayim gunlugu: DB kimligi (gercek yol + kaynak/yeni SHA) + asama; dayanikli ve atomik.

    `icerik_sha256` yalniz BUTUNLUK OZETIDIR (bozulma/yarim yazim tespiti); anahtarsizdir,
    kimlik dogrulayan bir imza DEGILDIR.
    """
    govde = dict(veri, surum=1, canonical=_kimlik_yolu(canonical_path))
    metin = json.dumps(govde, sort_keys=True, ensure_ascii=False)
    paket = {"gunluk": govde, "icerik_sha256": hashlib.sha256(metin.encode("utf-8")).hexdigest()}
    gecici = _gunluk_yolu(canonical_path) + ".tmp"
    with open(gecici, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(paket, sort_keys=True, ensure_ascii=False))
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(gecici, _gunluk_yolu(canonical_path))


def _gunluk_oku(canonical_path: str) -> Optional[dict]:
    yol = _gunluk_yolu(canonical_path)
    if not os.path.isfile(yol):
        return None
    try:
        with open(yol, encoding="utf-8") as fh:
            paket = json.load(fh)
        govde = paket["gunluk"]
        metin = json.dumps(govde, sort_keys=True, ensure_ascii=False)
        if hashlib.sha256(metin.encode("utf-8")).hexdigest() != paket.get("icerik_sha256"):
            raise ValueError("icerik_ozeti")
        return govde
    except (OSError, ValueError, KeyError, TypeError) as exc:
        raise GateRefused(f"ileri migration yayim gunlugu okunamadi/ozeti tutmuyor ({type(exc).__name__}) — "
                          "otomatik kurtarma yapilmadi", EXIT_RECOVERY_AMBIGUOUS) from exc


def _gunluk_sil(canonical_path: str) -> None:
    for yol in (_gunluk_yolu(canonical_path), _gunluk_yolu(canonical_path) + ".tmp"):
        if os.path.exists(yol):
            os.remove(yol)


def ileri_yarim_kalani_kurtar(canonical_path: str) -> Optional[str]:
    """Onceki ileri migration yayiminin yarida kalma izini DB KIMLIGINE bagli olarak toparlar.

    Yayim yontemi canonical'i ASLA tasimaz/silmez (yedek KOPYA ile alinir, degistirme atomiktir);
    bu yuzden kurtarma hicbir kosulda canonical'a yazmaz, yalniz yedek/gunluk/calisma kopyasini siler.
    Karar yayim gunluguyle verilir (gercek yol + kaynak/yeni SHA + asama; ozet denetimli):
    - Gunluk yok + prepublish yok → kalinti calisma kopyasi silinir (None).
    - Gunluk yok + prepublish VAR → kimliksiz yedek: DOKUNULMAZ, 56.
    - Gunluk baska bir DB yoluna ait / icerik ozeti tutmuyor / canonical YOK → 56.
    - YEDEKLENIYOR:
        canonical=kaynak → degistirme olmamis: (yarim olabilir) yedek silinir ("YAYIM_BASLAMADI").
        canonical=yeni, prepublish=kaynak → degistirme olmus: yedek silinir ("YAYIM_TAMAMLANDI").
    - YAYIMLANDI: canonical'a dokunulmaz; yedek (=kaynak) silinir ("YAYIM_TAMAMLANDI").
    - Diger her birlesim (SHA uyusmazligi dahil) → 56, hicbir dosyaya dokunulmaz.

    Cagrildigi yerler:
    - run_startup_gate_ileri() [acilis kilidi altinda, siniflandirmadan ONCE]
    """
    working, prepublish = _ileri_yollar(canonical_path)
    gunluk = _gunluk_oku(canonical_path)

    def belirsiz(neden: str) -> GateRefused:
        return GateRefused(f"ileri migration kurtarmasi belirsiz: {neden} — dosyalara dokunulmadi, "
                           "elle inceleme gerekir", EXIT_RECOVERY_AMBIGUOUS)

    if gunluk is None:
        if os.path.exists(prepublish):
            raise belirsiz("yayim gunlugu olmayan prepublish yedegi")
        if os.path.isfile(working):
            os.remove(working)
        return None
    if gunluk.get("canonical") != _kimlik_yolu(canonical_path):
        raise belirsiz("yayim gunlugu baska bir veritabani yoluna ait")
    if not os.path.isfile(canonical_path):
        raise belirsiz("yayim gunlugu var ama canonical yok (yontem canonical'i silmez); eski kopya geri konmaz")
    kaynak, yeni, asama = gunluk.get("kaynak_sha256"), gunluk.get("yeni_sha256"), gunluk.get("asama")
    pre_var = os.path.isfile(prepublish)
    can_sha = _sha256(canonical_path)
    pre_sha = _sha256(prepublish) if pre_var else None

    if asama == "YAYIMLANDI":
        if pre_var and pre_sha != kaynak:
            raise belirsiz("prepublish yedegi gunlukteki kaynakla eslesmiyor")
        sonuc = "YAYIM_TAMAMLANDI"
    elif asama == "YEDEKLENIYOR":
        if can_sha == kaynak:
            sonuc = "YAYIM_BASLAMADI"
        elif can_sha == yeni and pre_var and pre_sha == kaynak:
            sonuc = "YAYIM_TAMAMLANDI"
        else:
            raise belirsiz(f"asama YEDEKLENIYOR ile dosya durumu uyusmuyor (prepublish={pre_var})")
    else:
        raise belirsiz(f"bilinmeyen asama {asama!r}")
    if pre_var:
        os.remove(prepublish)
    if os.path.isfile(working):
        os.remove(working)
    _gunluk_sil(canonical_path)
    return sonuc


def ileri_migration_uygula(canonical_path: str, *, fault_at: Optional[str] = None) -> GateResult:
    """Canonical bastaki DB'yi CALISMA KOPYASINDA uygulama basina tasir ve atomik yayimlar.

    Adimlar: acilis kilidi → SQLite yan dosyasi yok → canonical sertifikasi → canonical'a YAZMAYI
    REDDEDEN tutamak (kopyadan degistirmeye kadar) → kaynak ozeti → dayanikli kopya → alembic
    upgrade → revizyon + yapi + saglik → eski tablolarin icerik ozetleri AYNI → canonical=kaynak
    (ek denetim; kilit tutamaktir) → yayim gunlugu (YEDEKLENIYOR) → yedek KOPYASI (=kaynak) →
    calisma kopyasina DELETE+yazma-reddeden tutamak → POSIX atomik degistirme → yeni canonical'a
    yazma-reddeden tutamak → gunluk YAYIMLANDI → yedek ve gunluk silinir → audit → tutamaklar birakilir.
    Canonical hicbir an yolsuz kalmaz ve kilit boyunca baska surec yazamaz.

    Cagrildigi yerler:
    - run_startup_gate_ileri()
    """
    if os.name != "nt":
        raise GateRefused("ileri migration yayim yazma kilidi yalniz Windows/NTFS'te uygulanir — "
                          "bu platformda yapilmadi", EXIT_PRECONDITION)
    with acilis_kilidi(canonical_path):
        return _ileri_migration_uygula_kilitli(canonical_path, fault_at=fault_at)


def _ileri_migration_uygula_kilitli(canonical_path: str, *, fault_at: Optional[str]) -> GateResult:
    _sqlite_yan_dosya_yok(canonical_path)
    if not is_certifiably_canonical(canonical_path):
        raise GateRefused("ileri migration yalniz sertifikali canonical bastan baslar",
                          EXIT_PRECONDITION)
    if not ar.is_alembic_available():
        raise GateRefused("alembic calistirilabiliri yok — ileri migration yapilmadi, HARD_STOP",
                          EXIT_ALEMBIC_UNAVAILABLE)
    working, prepublish = _ileri_yollar(canonical_path)
    for yol in (working, prepublish, _gunluk_yolu(canonical_path)):
        if os.path.exists(yol):
            raise GateRefused(f"ileri migration kalinti dosyasi mevcut: {os.path.basename(yol)}",
                              EXIT_FILESYSTEM)
    eski_kilit = _yazmayi_reddeden_tutamak(canonical_path)
    calisma_tutamagi = None
    yeni_kilit = None
    gunluk_yazildi = False
    degistirildi = False
    kaynak_sha = None
    try:
        kaynak_sha = _sha256(canonical_path)
        eski_tablolar = _tables(canonical_path) - {"alembic_version"}
        once = _tablo_icerik_ozetleri(canonical_path, eski_tablolar)
        _fault("ileri_calisma_kopyasi_oncesi", fault_at)
        _copy_bytes_durable(canonical_path, working)
        if _sha256(working) != kaynak_sha:
            raise GateRefused("ileri migration calisma kopyasi ozeti eslesmiyor", EXIT_FILESYSTEM)
        try:
            ar.alembic_upgrade(working, APPLICATION_HEAD)
        except ar.AlembicUnavailable as exc:
            raise GateRefused(f"alembic kullanilamiyor: {exc}", EXIT_ALEMBIC_UNAVAILABLE) from exc
        except RuntimeError as exc:
            raise GateRefused(f"ileri migration basarisiz: {exc}", EXIT_FORWARD_MIGRATION_FAILED) from exc
        _fault("ileri_migration_sonrasi", fault_at)
        if not is_certifiably_application_head(working):
            raise GateRefused("ileri migration sonrasi uygulama basi sertifikalanamadi",
                              EXIT_FORWARD_MIGRATION_FAILED)
        butunluk, fk = _health(working)
        if butunluk != "ok" or fk:
            raise GateRefused(f"ileri migration sonrasi saglik: integrity={butunluk} fk={fk}",
                              EXIT_FORWARD_MIGRATION_FAILED)
        sonra = _tablo_icerik_ozetleri(working, eski_tablolar)
        if sonra != once:
            degisen = sorted(t for t in once if once[t] != sonra.get(t))
            raise GateRefused(f"ileri migration eski tablo verisini degistirdi: {degisen}",
                              EXIT_FORWARD_MIGRATION_FAILED)
        _fsync_close(working)
        yeni_sha = _sha256(working)
        _fault("ileri_yayim_oncesi", fault_at)
        _sqlite_yan_dosya_yok(canonical_path)
        if _sha256(canonical_path) != kaynak_sha:  # ek denetim; koruma yazmayi reddeden tutamaktir
            raise GateRefused("canonical ileri migration SIRASINDA degisti — yayim yapilmadi", EXIT_FILESYSTEM)
        if not _ayni_birimde(working, canonical_path):
            raise GateRefused("calisma kopyasi canonical ile ayni klasor/birimde degil — atomik yayim yok",
                              EXIT_FILESYSTEM)
        _fault("ileri_son_sha_sonrasi", fault_at)
        _gunluk_yaz(canonical_path, {"asama": "YEDEKLENIYOR", "kaynak_sha256": kaynak_sha,
                                     "yeni_sha256": yeni_sha})
        gunluk_yazildi = True
        _fault("ileri_gunluk_yazildi", fault_at)
        _copy_bytes_durable(canonical_path, prepublish)
        if _sha256(prepublish) != kaynak_sha:
            raise GateRefused("prepublish yedek kopyasi ozeti eslesmiyor", EXIT_FILESYSTEM)
        _fault("ileri_yedek_kopyalandi", fault_at)
        calisma_tutamagi = _yazmayi_reddeden_tutamak(working, silme_erisimi=True)
        _posix_atomik_degistir(calisma_tutamagi, canonical_path)
        degistirildi = True
        # Yeni canonical: DELETE erisimli tutamak (okuyuculari da engeller) kapanmadan once
        # salt-okunur yazma-reddeden tutamak alinir → yazma penceresi olusmaz.
        yeni_kilit = _yazmayi_reddeden_tutamak(canonical_path)
        _tutamak_kapat(calisma_tutamagi)
        calisma_tutamagi = None
        _fault("ileri_yayim_gunluk_oncesi", fault_at)
        _gunluk_yaz(canonical_path, {"asama": "YAYIMLANDI", "kaynak_sha256": kaynak_sha,
                                     "yeni_sha256": yeni_sha})
        _fault("ileri_yedek_silme_oncesi", fault_at)
        _tutamak_kapat(eski_kilit)  # yerine gecilmis eski dosya (bu tutamakla birlikte) kaybolur
        eski_kilit = None
        os.remove(prepublish)
        _gunluk_sil(canonical_path)
        gunluk_yazildi = False
        _fault("ileri_yayim_sonrasi", fault_at)
        rapor = GateResult(
            state=BootstrapState.VERIFIED_APPLICATION_HEAD,
            action="FORWARD_MIGRATED",
            terminal_revision=APPLICATION_HEAD,
            heads=ar.alembic_heads_count(canonical_path),
            integrity_check=butunluk,
            foreign_key_violations=fk,
            row_counts=_row_counts(canonical_path),
            details={"kaynak_revizyon": CANONICAL_HEAD, "eski_tablo_sayisi": len(eski_tablolar),
                     "eski_tablo_icerikleri_ayni": True, "yayim_kilidi": "windows_yazmayi_reddeden_tutamak"},
        )
        _write_bootstrap_audit(canonical_path, {
            "action": rapor.action,
            "terminal_revision": rapor.terminal_revision,
            "source_revision": CANONICAL_HEAD,
            "source_sha256": kaynak_sha,
            "heads": rapor.heads,
        })
        return rapor
    except (InjectedFault, GateRefused):
        raise
    except OSError as exc:
        raise GateRefused(f"dosya sistemi hatasi: {type(exc).__name__}: {exc}", EXIT_FILESYSTEM) from exc
    finally:
        _tutamak_kapat(calisma_tutamagi)
        # Degistirme olmadiysa yarim yayim izleri canonical KILITLIYKEN temizlenir.
        try:
            if gunluk_yazildi and not degistirildi and kaynak_sha is not None \
                    and _sha256(canonical_path) == kaynak_sha:
                if os.path.exists(prepublish):
                    os.remove(prepublish)
                _gunluk_sil(canonical_path)
        except OSError:
            pass
        _tutamak_kapat(yeni_kilit)
        _tutamak_kapat(eski_kilit)
        if os.path.exists(working):
            try:
                os.remove(working)
            except OSError:
                pass


def run_startup_gate_ileri(
    canonical_path: str, *, legacy_hint_path: Optional[str] = None,
    fault_at: Optional[str] = None,
) -> GateResult:
    """Baslangic kapisi + kontrollu ileri migration (paketli runtime giris noktasi).

    Tum calisma surecler arasi acilis kilidi altindadir (ikinci acilis 55). run_startup_gate()
    davranisi DEGISMEZ; o canonical basa (CANONICAL_HEAD) ulastiginda (taze kurulum, legacy
    adoption ya da mevcut canonical) ileri migration uygulanir. Zaten uygulama basindaki DB
    yalniz sertifikalanir (tekrar acilis). Yarim kalan yayim once kimlige bagli toparlanir.

    Cagrildigi yerler:
    - backend/run_server.py::_run_startup_schema_gate()
    - tests/test_startup_gate_ileri_migration.py
    """
    with acilis_kilidi(canonical_path):
        kurtarma = ileri_yarim_kalani_kurtar(canonical_path)
        rapor = run_startup_gate(canonical_path, legacy_hint_path=legacy_hint_path,
                                 fault_at=None if fault_at in ILERI_FAULT_POINTS else fault_at)
        if rapor.terminal_revision == CANONICAL_HEAD:
            onceki = rapor.action
            rapor = ileri_migration_uygula(canonical_path, fault_at=fault_at)
            rapor.details["onceki_eylem"] = onceki
        if kurtarma:
            rapor.details["yarim_kalan_yayim"] = kurtarma
        return rapor


# ── Audit (DB'nin DISINDA, sanitize) ─────────────────────────────────────
def _audit_path(canonical_path: str) -> str:
    return canonical_path + AUDIT_SUFFIX


def _write_bootstrap_audit(canonical_path: str, payload: dict) -> None:
    metin = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True)
    imza = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    with open(_audit_path(canonical_path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(
            {"audit": payload, "audit_sha256": imza, "audit_version": AUDIT_VERSION},
            indent=1, ensure_ascii=False, sort_keys=True,
        ))


def read_bootstrap_audit(canonical_path: str) -> Optional[dict]:
    yol = _audit_path(canonical_path)
    if not os.path.isfile(yol):
        return None
    try:
        with open(yol, encoding="utf-8") as fh:
            paket = json.load(fh)
        beklenen = hashlib.sha256(
            json.dumps(paket["audit"], indent=1, ensure_ascii=False, sort_keys=True)
            .encode("utf-8")
        ).hexdigest()
        if beklenen != paket.get("audit_sha256"):
            return None
        return paket["audit"]
    except (OSError, ValueError, KeyError):
        return None


# ── STEP 1+3: ust-seviye orkestrator (run_server.py buradan cagirir) ─────
def run_startup_gate(
    canonical_path: str, *, legacy_hint_path: Optional[str] = None,
    fault_at: Optional[str] = None,
) -> GateResult:
    """
    TEK giris noktasi. HICBIR dal varsayilan/bilinmeyen olarak devam ETMEZ —
    her durum ya basariyla GateResult doner ya GateRefused firlatir.

    Cagrildigi yerler:
    - backend/run_server.py::main() [PDSMR-R3, app.main import'undan ONCE]
    """
    durum = classify_startup_state(canonical_path, legacy_hint_path=legacy_hint_path)

    if durum is BootstrapState.DB_ABSENT:
        return fresh_initialize(canonical_path, fault_at=fault_at)

    if durum is BootstrapState.VERIFIED_LEGACY_013:
        return perform_controlled_adoption(canonical_path, fault_at=fault_at)

    if durum is BootstrapState.VERIFIED_CANONICAL_HEAD:
        return certify_canonical(canonical_path)

    if durum is BootstrapState.VERIFIED_APPLICATION_HEAD:
        return certify_application_head(canonical_path)

    if durum is BootstrapState.PARTIAL_S5_SCHEMA:
        raise GateRefused(
            "013 revizyonunda ama S5 tablolari mevcut — create_all fail-open "
            "izi (PDSMR-R2I bulgusu). Otomatik onarim YOK, elle inceleme GEREKIR.",
            EXIT_HARD_STOP_PARTIAL_SCHEMA, state=durum,
        )

    if durum is BootstrapState.CANONICAL_ABSENT_LEGACY_EXISTS:
        raise GateRefused(
            "legacy DB var ama canonical yok — pre-upgrade kurtarma (PDSMR-R2) "
            "atlanmis veya basarisiz olmus olabilir",
            EXIT_HARD_STOP_CANONICAL_ABSENT_LEGACY_EXISTS, state=durum,
        )

    # UNKNOWN_SCHEMA ve tanimlanmamis HER SEY buraya duser — fail-closed.
    raise GateRefused(
        f"taninmayan/bozuk sema durumu: {durum.value}",
        EXIT_HARD_STOP_UNKNOWN_SCHEMA, state=durum,
    )


__all__ = [
    "AUDIT_SUFFIX",
    "EXIT_ADOPTION_FAILED",
    "EXIT_ALEMBIC_UNAVAILABLE",
    "APPLICATION_HEAD",
    "EXIT_CERTIFICATION_FAILED",
    "EXIT_FORWARD_MIGRATION_FAILED",
    "EXIT_CONCURRENT_OR_BUSY",
    "EXIT_RECOVERY_AMBIGUOUS",
    "ILERI_JOURNAL_SUFFIX",
    "ILERI_LOCK_SUFFIX",
    "acilis_kilidi",
    "ILERI_FAULT_POINTS",
    "EXIT_FILESYSTEM",
    "EXIT_FRESH_INIT_FAILED",
    "EXIT_HARD_STOP_AUDIT_MISSING_OR_INVALID",
    "EXIT_HARD_STOP_CANONICAL_ABSENT_LEGACY_EXISTS",
    "EXIT_HARD_STOP_LEGACY_VALIDATION_FAILED",
    "EXIT_HARD_STOP_PARTIAL_SCHEMA",
    "EXIT_HARD_STOP_UNKNOWN_SCHEMA",
    "EXIT_OK",
    "EXIT_PRECONDITION",
    "EXIT_UNEXPECTED",
    "GATE_FAULT_POINTS",
    "BootstrapState",
    "GateRefused",
    "GateResult",
    "InjectedFault",
    "certify_application_head",
    "certify_canonical",
    "ileri_migration_uygula",
    "ileri_yarim_kalani_kurtar",
    "is_certifiably_application_head",
    "classify_startup_state",
    "fresh_initialize",
    "perform_controlled_adoption",
    "read_bootstrap_audit",
    "run_startup_gate",
    "run_startup_gate_ileri",
]
