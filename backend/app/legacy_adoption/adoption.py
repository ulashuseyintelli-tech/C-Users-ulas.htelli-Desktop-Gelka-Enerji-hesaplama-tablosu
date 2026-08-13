"""
PDSMR-R1D / FAZ 3 — KONTROLLU LEGACY ADOPTION (YALNIZ DISPOSABLE KOPYA).

Akis:
  0) yol guvenligi (fiziksel kimlik, sadece metin degil)
  1) degismez kaynak + ayri rollback yedegi + ayri calisma kopyasi
  2) onarim sozlesmesi (repair.py) — yalniz calisma kopyasinda
  3) revizyon etki esdegerligi (lineage.py) — salt-okunur siniflandirma
  4) kontrollu lineage uzlastirmasi — YALNIZ A/B revizyonlari isaretlenir
  5) kalan C revizyonlari NORMAL alembic ile calistirilir
  6) sertifikasyon
  7) idempotentlik

YASAKLAR (kod duzeyinde zorlanir):
  - gercek production yolu, kurulu uygulama dizini, kaynak/yedek yollari
  - genel amacli stamp / serbest revizyon argumani
  - create_all ile tablo yaratma
  - S5 tablolarini elle yaratma

Cagrildigi yerler:
- tests/test_legacy_adoption_phase3.py [PDSMR-R1D/Faz3 provasi]
  (Bilincli olarak HICBIR router/CLI/startup yolundan cagrilmaz.)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import asdict, dataclass, field
from typing import Any, Optional

from . import policy
from .lineage import (
    CANONICAL_BRANCH,
    CANONICAL_HEAD,
    PRODUCTION_BRANCH_TIP,
    EffectClass,
    RevisionClassification,
    classify_canonical_branch,
)
from .repair import repair_incidents_canonical

AUDIT_SUFFIX = ".pdsmr-adoption-audit.json"
AUDIT_VERSION = "PDSMR-R1D/PHASE3/1"

# Fault injection noktalari — YALNIZ test icin; uretim yolu yoktur.
FAULT_POINTS = (
    "before_repair", "after_repair",
    "before_lineage", "after_lineage", "during_forward",
)


class AdoptionRefused(Exception):
    """On-kosul saglanmadi. Hedef DB'ye anlamli bir degisiklik YAPILMADI."""


class InjectedFault(Exception):
    """Test amacli kesinti. Gercek bir arizayi taklit eder."""


@dataclass
class AdoptionReport:
    outcome: str  # "ADOPTED" | "ALREADY_ADOPTED"
    classifications: tuple[RevisionClassification, ...] = ()
    stamped: tuple[str, ...] = ()
    migrated: tuple[str, ...] = ()
    terminal_revision: str = ""
    heads: int = 0
    integrity_check: str = ""
    foreign_key_violations: int = -1
    row_counts: dict[str, int] = field(default_factory=dict)
    source_sha256: str = ""
    rollback_sha256: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ── Yol guvenligi ───────────────────────────────────────────────────────
def _real(path: str) -> str:
    """Sembolik bag / junction / gorece yol / buyuk-kucuk harf normalize."""
    return os.path.normcase(os.path.realpath(os.path.abspath(path)))


def _same_file(a: str, b: str) -> bool:
    """
    Ayni dosya mi? Once isletim sistemi kimligi (inode/file-id), o
    calismazsa cozulmüs yol karsilastirmasi.

    Windows'ta salt METIN karsilastirmasi YETMEZ: junction, kisa (8.3)
    ad ve gorece yol ayni dosyaya farkli metinlerle isaret edebilir.
    """
    try:
        if os.path.exists(a) and os.path.exists(b):
            return os.path.samefile(a, b)
    except OSError:
        pass
    return _real(a) == _real(b)


def _assert_safe_working_target(
    working: str, *, source: str, rollback: Optional[str] = None
) -> None:
    real = _real(working)

    for marker in policy.FORBIDDEN_REPAIR_PATH_MARKERS:
        if os.path.normcase(marker) in real:
            raise AdoptionRefused(
                f"hedef kurulu uygulama alaninda (marker={marker!r})"
            )

    parent = os.path.dirname(real)
    if not os.path.isdir(parent):
        raise AdoptionRefused("hedefin ust dizini yok — kimlik kanitlanamiyor")

    if _same_file(working, source):
        raise AdoptionRefused("calisma kopyasi KAYNAK ile ayni dosya")
    if rollback and _same_file(working, rollback):
        raise AdoptionRefused("calisma kopyasi ROLLBACK yedegi ile ayni dosya")


def _assert_is_legacy_family(db_path: str) -> None:
    """
    Hedefin BU legacy ailesi oldugunu kanitlar.

    Dogru revizyon numarasini tasiyan alakasiz bir DB, yalnizca revizyon
    kontrolunu gecebilir. Tablo kumesi de birebir eslesmelidir; aksi halde
    onarim/adoption alakasiz bir semayi donusturmeye kalkar ve anlasilmaz
    bir SQLite hatasiyla duser.
    """
    from .validator import _load_model_metadata

    md = _load_model_metadata()
    beklenen = (
        (set(md.tables) - policy.EXPECTED_ABSENT_MODEL_TABLES)
        | policy.KNOWN_LEGACY_ONLY_TABLES
    )
    con = sqlite3.connect(_ro(db_path), uri=True)
    try:
        mevcut = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
    finally:
        con.close()
    if mevcut != beklenen:
        eksik = sorted(beklenen - mevcut)
        fazla = sorted(mevcut - beklenen)
        raise AdoptionRefused(
            f"hedef bu legacy ailesi DEGIL: eksik={eksik[:6]} fazla={fazla[:6]}"
        )


def _effect_present(db_path: str, eff) -> bool:
    """Bir revizyonun etkisini SALT-OKUNUR baglantida probe eder."""
    con = sqlite3.connect(_ro(db_path), uri=True)
    try:
        return bool(eff.present(con))
    finally:
        con.close()


def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _ro(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def _health(path: str) -> tuple[str, int]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return (
            con.execute("PRAGMA integrity_check").fetchone()[0],
            len(con.execute("PRAGMA foreign_key_check").fetchall()),
        )
    finally:
        con.close()


def _revisions(path: str) -> tuple[str, ...]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return tuple(sorted(
            r[0] for r in con.execute("SELECT version_num FROM alembic_version").fetchall()
        ))
    finally:
        con.close()


def _row_counts(path: str) -> dict[str, int]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        tablolar = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        return {
            t: con.execute(f"SELECT COUNT(*) FROM {t}").fetchone()[0]
            for t in sorted(policy.EXPECTED_ROW_COUNTS) if t in tablolar
        }
    finally:
        con.close()


# ── Alembic surumu (ALT SUREC) ──────────────────────────────────────────
# DIKKAT: alembic IN-PROCESS import EDILEMEZ. `backend/` sys.path'te
# oldugundan `backend/alembic/` (migration dizini) kurulu `alembic`
# paketini GOLGELER ve `from alembic import command` ImportError verir.
# Konsol script'i alt surecte dogru paketi cozer; ayrica migration'lar
# temiz bir yorumlayicida calisir.
def _backend_dir() -> str:
    # __file__ = backend/app/legacy_adoption/adoption.py -> uc kez yukari
    return os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def _alembic_exe() -> str:
    import sys

    kok = os.path.dirname(sys.executable)
    for ad in ("alembic.exe", "alembic"):
        aday = os.path.join(kok, ad)
        if os.path.isfile(aday):
            return aday
    bulunan = shutil.which("alembic")
    if not bulunan:
        raise AdoptionRefused("alembic calistirilabiliri bulunamadi")
    return bulunan


def _run_alembic(db_path: str, *args: str) -> str:
    import subprocess

    env = dict(os.environ)
    env["DATABASE_URL"] = "sqlite:///" + db_path.replace("\\", "/")
    sonuc = subprocess.run(
        [_alembic_exe(), *args],
        cwd=_backend_dir(), env=env, capture_output=True, text=True,
    )
    if sonuc.returncode != 0:
        kuyruk = (sonuc.stderr or sonuc.stdout).strip().splitlines()[-6:]
        raise AdoptionRefused(
            f"alembic {' '.join(args)} basarisiz (exit={sonuc.returncode}): "
            + " | ".join(kuyruk)
        )
    return sonuc.stdout


def _alembic_upgrade(db_path: str, revision: str) -> None:
    _run_alembic(db_path, "upgrade", revision)


def _alembic_heads_count(db_path: str) -> int:
    cikti = _run_alembic(db_path, "heads")
    return len([s for s in cikti.splitlines() if s.strip()])


# ── Kontrollu lineage uzlastirmasi ──────────────────────────────────────
def _mark_revision_applied(db_path: str, *, add: str, replace: Optional[str]) -> None:
    """
    alembic_version satirlarini TEK, ATOMIK islemde yonetir.

    Bu, "genel amacli stamp" DEGILDIR: revizyon adi cagirandan serbestce
    alinmaz; yalnizca A/B olarak KANITLANMIS revizyonlar icin bu modulun
    icinden cagrilir. Islem ya tamamen uygulanir ya hic uygulanmaz.
    """
    con = sqlite3.connect(db_path)
    con.isolation_level = None
    try:
        con.execute("BEGIN IMMEDIATE")
        try:
            if replace is not None:
                silindi = con.execute(
                    "DELETE FROM alembic_version WHERE version_num=?", (replace,)
                ).rowcount
                if silindi != 1:
                    raise AdoptionRefused(
                        f"beklenen revizyon satiri bulunamadi: {replace}"
                    )
            var = con.execute(
                "SELECT COUNT(*) FROM alembic_version WHERE version_num=?", (add,)
            ).fetchone()[0]
            if not var:
                con.execute("INSERT INTO alembic_version (version_num) VALUES (?)", (add,))
            con.execute("COMMIT")
        except Exception:
            con.execute("ROLLBACK")
            raise
    finally:
        con.close()


# ── Audit (DB'nin DISINDA) ──────────────────────────────────────────────
def _audit_path(working: str) -> str:
    return working + AUDIT_SUFFIX


def _write_audit(working: str, payload: dict) -> None:
    metin = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True)
    imza = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    with open(_audit_path(working), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(
            {"audit": payload, "audit_sha256": imza, "audit_version": AUDIT_VERSION},
            indent=1, ensure_ascii=False, sort_keys=True,
        ))


def read_audit(working: str) -> Optional[dict]:
    """Audit kaydini okur ve imzasini dogrular. Bozuksa None doner."""
    yol = _audit_path(working)
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


def _is_certifiably_adopted(path: str) -> bool:
    """
    DB'nin BAGIMSIZ OLARAK canonical oldugunu kanitlar (audit'e guvenmez).

    Bozuk/eksik audit tek basina yeniden calistirmayi tetiklememelidir;
    ancak DB durumu tam olarak sertifikalanabiliyorsa no-op guvenlidir.
    """
    if _revisions(path) != (CANONICAL_HEAD,):
        return False
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        tablolar = {
            r[0] for r in con.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            ).fetchall()
        }
        if not policy.EXPECTED_ABSENT_MODEL_TABLES.issubset(tablolar):
            return False
        kolonlar = {r[1] for r in con.execute("PRAGMA table_info(prospect_companies)").fetchall()}
        if not {c for _t, c in policy.EXPECTED_ABSENT_MODEL_COLUMNS}.issubset(kolonlar):
            return False
        idx = {r[1] for r in con.execute("PRAGMA index_list(incidents)").fetchall()}
        if not {s["name"] for s in policy.REQUIRED_CANONICAL_INDEXES["incidents"]}.issubset(idx):
            return False
        if con.execute("PRAGMA integrity_check").fetchone()[0] != "ok":
            return False
        if con.execute("PRAGMA foreign_key_check").fetchall():
            return False
    finally:
        con.close()
    return True


def _fault(point: str, fault_at: Optional[str]) -> None:
    if fault_at == point:
        raise InjectedFault(f"enjekte edilmis kesinti: {point}")


# ── Ana akis ────────────────────────────────────────────────────────────
def adopt_legacy_copy(
    working_path: str,
    *,
    source_path: str,
    rollback_path: str,
    expected_source_sha256: str,
    confirm_disposable_copy: bool = False,
    fault_at: Optional[str] = None,
) -> AdoptionReport:
    """
    Dogrulanmis bir DISPOSABLE kopyayi canonical head'e adopt eder.

    Args:
        working_path: Mutasyona ugrayacak calisma kopyasi.
        source_path: Degismez kaynak (uretim kopyasi). ASLA yazilmaz.
        rollback_path: Ayri geri-donus yedegi. ASLA yazilmaz.
        expected_source_sha256: Beklenen kaynak parmak izi; sapma -> ret.
        confirm_disposable_copy: Cagiranin acik onayi.
        fault_at: YALNIZ test — belirtilen noktada kesinti enjekte eder.

    Raises:
        AdoptionRefused: on-kosul saglanmadi.
        InjectedFault: test kesintisi.

    Cagrildigi yerler:
    - tests/test_legacy_adoption_phase3.py [PDSMR-R1D/Faz3]
    """
    if not confirm_disposable_copy:
        raise AdoptionRefused("confirm_disposable_copy=True verilmedi")
    if fault_at is not None and fault_at not in FAULT_POINTS:
        raise AdoptionRefused(f"bilinmeyen fault noktasi: {fault_at}")

    for yol, ad in ((source_path, "kaynak"), (rollback_path, "rollback")):
        if not os.path.isfile(yol):
            raise AdoptionRefused(f"{ad} dosyasi yok")
    if not os.path.isfile(working_path):
        raise AdoptionRefused("calisma kopyasi yok")

    _assert_safe_working_target(working_path, source=source_path, rollback=rollback_path)
    if _same_file(source_path, rollback_path):
        raise AdoptionRefused("kaynak ile rollback ayni dosya")

    kaynak_hash = _sha256(source_path)
    if kaynak_hash != expected_source_sha256:
        raise AdoptionRefused(
            "kaynak parmak izi beklenenden FARKLI — adoption reddedildi"
        )
    rollback_hash = _sha256(rollback_path)
    if rollback_hash != kaynak_hash:
        raise AdoptionRefused("rollback yedegi kaynakla ayni degil")

    for yol, ad in ((source_path, "kaynak"), (rollback_path, "rollback"),
                    (working_path, "calisma")):
        butunluk, fk = _health(yol)
        if butunluk != "ok":
            raise AdoptionRefused(f"{ad}: integrity_check={butunluk}")
        if fk:
            raise AdoptionRefused(f"{ad}: {fk} FK ihlali")

    # ── Idempotentlik: zaten adopt edilmis mi ──────────────────────────
    if _is_certifiably_adopted(working_path):
        return AdoptionReport(
            outcome="ALREADY_ADOPTED",
            terminal_revision=CANONICAL_HEAD,
            heads=_alembic_heads_count(working_path),
            source_sha256=kaynak_hash,
            rollback_sha256=rollback_hash,
            row_counts=_row_counts(working_path),
            details={"audit_present": read_audit(working_path) is not None},
        )

    if _revisions(working_path) != (PRODUCTION_BRANCH_TIP,):
        raise AdoptionRefused(
            f"beklenen pre-adoption revizyonu {PRODUCTION_BRANCH_TIP} degil: "
            f"{_revisions(working_path)}"
        )
    _assert_is_legacy_family(working_path)

    # ── 2) Onarim ──────────────────────────────────────────────────────
    _fault("before_repair", fault_at)
    onarim = repair_incidents_canonical(working_path, confirm_disposable_copy=True)
    _fault("after_repair", fault_at)

    # ── 3) Etki esdegerligi ────────────────────────────────────────────
    siniflar = classify_canonical_branch(working_path)
    catismalar = [c for c in siniflar if c.effect_class is EffectClass.CONFLICT_OR_UNKNOWN]
    if catismalar:
        raise AdoptionRefused(
            "revizyon etki catismasi: "
            + "; ".join(f"{c.revision}: {c.conflict}" for c in catismalar)
        )

    # ── 4+5) Graf sirasinda: A/B isaretle, C calistir ──────────────────
    _fault("before_lineage", fault_at)
    stamped: list[str] = []
    migrated: list[str] = []
    # Canonical dalin alembic_version'daki mevcut ucu. None = dal henuz
    # eklenmedi (uretim dali `013` tek basina duruyor).
    branch_tip: Optional[str] = None

    for sinif, eff in zip(siniflar, CANONICAL_BRANCH):
        if sinif.effect_class in (
            EffectClass.EXACT_EFFECT_ALREADY_PRESENT,
            EffectClass.ACCEPTED_VERIFIED_VARIANT,
        ):
            # Etki VAR: revizyonu isaretle, CALISTIRMA.
            _mark_revision_applied(working_path, add=eff.revision, replace=branch_tip)
            branch_tip = eff.revision
            stamped.append(eff.revision)
            if len(stamped) == 1:
                _fault("after_lineage", fault_at)
        else:
            # Etki YOK: NORMAL alembic ile calistir.
            if migrated:
                _fault("during_forward", fault_at)
            _alembic_upgrade(working_path, eff.revision)
            if not _effect_present(working_path, eff):
                raise AdoptionRefused(
                    f"{eff.revision} calisti ama etkisi olusmadi: {eff.summary}"
                )
            branch_tip = eff.revision
            migrated.append(eff.revision)

    # ── Merge revizyonu: normal upgrade ────────────────────────────────
    _alembic_upgrade(working_path, CANONICAL_HEAD)
    migrated.append(CANONICAL_HEAD)

    # ── 6) Sertifikasyon ───────────────────────────────────────────────
    revizyonlar = _revisions(working_path)
    if revizyonlar != (CANONICAL_HEAD,):
        raise AdoptionRefused(f"terminal revizyon {CANONICAL_HEAD} degil: {revizyonlar}")
    butunluk, fk = _health(working_path)
    if butunluk != "ok" or fk:
        raise AdoptionRefused(f"adoption sonrasi integrity={butunluk} fk={fk}")
    if _sha256(source_path) != kaynak_hash:
        raise AdoptionRefused("KAYNAK DEGISTI — adoption gecersiz")
    if _sha256(rollback_path) != rollback_hash:
        raise AdoptionRefused("ROLLBACK yedegi DEGISTI — adoption gecersiz")

    rapor = AdoptionReport(
        outcome="ADOPTED",
        classifications=siniflar,
        stamped=tuple(stamped),
        migrated=tuple(migrated),
        terminal_revision=CANONICAL_HEAD,
        heads=_alembic_heads_count(working_path),
        integrity_check=butunluk,
        foreign_key_violations=fk,
        row_counts=_row_counts(working_path),
        source_sha256=kaynak_hash,
        rollback_sha256=rollback_hash,
        details={
            "repair_outcome": onarim.outcome,
            "repair_indexes_created": list(onarim.indexes_created),
        },
    )
    _write_audit(working_path, {
        "outcome": rapor.outcome,
        "terminal_revision": rapor.terminal_revision,
        "stamped": list(rapor.stamped),
        "migrated": list(rapor.migrated),
        "source_sha256": rapor.source_sha256,
        "rollback_sha256": rapor.rollback_sha256,
        "row_counts": rapor.row_counts,
        "classifications": [asdict(c) | {"effect_class": c.effect_class.value}
                            for c in siniflar],
    })
    return rapor


__all__ = [
    "AUDIT_SUFFIX",
    "AdoptionRefused",
    "AdoptionReport",
    "FAULT_POINTS",
    "InjectedFault",
    "adopt_legacy_copy",
    "read_audit",
]
