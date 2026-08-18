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
    "EXIT_CERTIFICATION_FAILED",
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
    "certify_canonical",
    "classify_startup_state",
    "fresh_initialize",
    "perform_controlled_adoption",
    "read_bootstrap_audit",
    "run_startup_gate",
]
