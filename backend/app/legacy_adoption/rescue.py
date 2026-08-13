"""
PDSMR-R2 — installer ON-UPGRADE (pre-cleanup) veritabani kurtarma.

NSIS/electron-builder'in `resources/backend/gelka_enerji.db`'yi silmesinden
ONCE calisir ve legacy DB'yi Electron userData altindaki canonical konuma
tasir. Uygulama-baslangici relocation'i BILEREK YETERSIZ sayilir (installer
resources dizinini uygulama hic baslamadan silebilir) — kurtarma installer
YASAM DONGUSUNUN icinde, temizlikten ONCE tamamlanmis OLMALIDIR.

Yedi durumlu kesin oncelik politikasi (owner karari, PDSMR-R2):
  A) legacy var, canonical yok            -> kurtarilabilir
  B) legacy yok, canonical var            -> no-op (canonical ASLA ezilmez)
  C) ikisi de yok                          -> no-op (taze kurulum)
  D) ikisi de var, hash ESIT               -> no-op (dogrulanmis esdegerlik)
  E) ikisi de var, hash FARKLI             -> HARD_STOP, ikisi de korunur
  F) canonical var ama gecersiz/okunamiyor -> HARD_STOP, sessizce degistirilmez
  G) legacy var ama gecersiz/okunamiyor    -> HARD_STOP

On kosul saglanmadan HICBIR mutasyon YAPILMAZ. Islem sirasi hep:
  ro-ac -> aile+butunluk dogrula -> rollback yedegi -> hedefte temp kopya ->
  temp dogrula -> atomik rename -> canonical'i tekrar ac+dogrula ->
  DB DISINDA imzali journal yaz.

Legacy kaynak BU MODUL TARAFINDAN ASLA silinmez/degistirilmez — installer'in
sonraki temizlik adimi onu kurtarma DOGRULANMIS basari donduktan SONRA
kaldirabilir.

Cagrildigi yerler:
- tests/test_legacy_adoption_rescue.py [PDSMR-R2 provasi]
  (Bilincli olarak HICBIR router/CLI/uygulama-baslangici yolundan
   cagrilmaz. Gercek cagiran electron/build/installer.nsh icindeki
   customInit hook'udur — bu turda GERCEK installer derlenmez/calistirilmaz.)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Optional

from . import policy
from .pathsafety import is_forbidden_target, same_file

JOURNAL_SUFFIX = ".pdsmr-rescue-journal.json"
JOURNAL_VERSION = "PDSMR-R2/RESCUE/1"

FAULT_POINTS = (
    "before_backup", "after_backup",
    "before_temp_copy", "after_temp_copy",
    "before_rename", "after_rename",
    "before_journal",
)


class RescueRefused(Exception):
    """On-kosul saglanmadi ya da HARD_STOP durumu. Hedefe anlamli yazma YAPILMADI."""


class InjectedFault(Exception):
    """Test amacli kesinti. Gercek bir ariza/kesintiyi taklit eder."""


class PrecedenceState(str, Enum):
    LEGACY_ELIGIBLE = "A_LEGACY_ELIGIBLE"
    CANONICAL_ALREADY_PRESENT = "B_CANONICAL_ALREADY_PRESENT_NOOP"
    FRESH_INSTALL = "C_FRESH_INSTALL_NOOP"
    EQUIVALENT = "D_EQUIVALENT_NOOP"
    CONFLICT = "E_CONFLICT_HARD_STOP"
    CANONICAL_INVALID = "F_CANONICAL_INVALID_HARD_STOP"
    LEGACY_INVALID = "G_LEGACY_INVALID_HARD_STOP"


_NOOP_STATES = (
    PrecedenceState.CANONICAL_ALREADY_PRESENT,
    PrecedenceState.FRESH_INSTALL,
    PrecedenceState.EQUIVALENT,
)
_HARD_STOP_STATES = (
    PrecedenceState.CONFLICT,
    PrecedenceState.CANONICAL_INVALID,
    PrecedenceState.LEGACY_INVALID,
)


@dataclass(frozen=True)
class PrecedenceClassification:
    state: PrecedenceState
    detail: str
    legacy_sha256: Optional[str] = None
    canonical_sha256: Optional[str] = None


@dataclass
class RescueReport:
    outcome: str  # "RESCUED" | "NOOP_CANONICAL_PRESENT" | "NOOP_FRESH_INSTALL" | "NOOP_EQUIVALENT"
    precedence: PrecedenceState = PrecedenceState.FRESH_INSTALL
    canonical_path: str = ""
    backup_path: str = ""
    source_sha256: str = ""
    integrity_check: str = ""
    foreign_key_violations: int = -1
    details: dict[str, Any] = field(default_factory=dict)


# ── Salt-okunur sonda yardimcilari ──────────────────────────────────────
def _sha256(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def _copy_bytes_durable(src: str, dst: str) -> None:
    """
    DUZ byte kopyasi + fsync.

    SQLite'in Online Backup API'si (`Connection.backup()`) BILEREK
    KULLANILMAZ: backup her calistiginda hedefte bir COMMIT olusturur ve
    bu, header'daki "file change counter" / "version-valid-for number"
    alanlarini DEGISTIRIR — sayfa ICERIGI ayni kalsa da SONUC dosyanin
    SHA256'si kaynaktan FARKLI cikar (olculdu, PDSMR-R2). Kaynak zaten
    KAPALI bir dosya oldugu icin (installer senaryosunda uygulama
    calismiyor; -wal/-journal yoklugu _is_readable_family_db'de ayrica
    dogrulanir) duz byte kopyasi BYTE-IDENTICAL bir sonuc verir ve
    SQLite'in ic transaction semantigine hic bagli degildir.
    """
    with open(src, "rb") as kaynak, open(dst, "wb") as hedef:
        shutil.copyfileobj(kaynak, hedef, length=1 << 20)
        hedef.flush()
        os.fsync(hedef.fileno())


def _ro(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def _is_readable_family_db(path: str) -> tuple[bool, str]:
    """
    Dosya acilabilir bir SQLite mi VE bu legacy ailesinden mi?

    Tam Faz 2 validator'ini CALISTIRMAZ (o adoption uygunlugu icindir);
    burada amac yalnizca "bu bizim DB ailemiz mi, bozuk mu" sorusudur.
    """
    if not os.path.isfile(path):
        return False, "dosya yok"
    for ek in ("-wal", "-journal"):
        if os.path.exists(path + ek):
            # Checkpoint edilmemis/yarim transaction olasiligi: DUZ BYTE
            # kopyasi bu durumda tutarsiz bir goruntu alabilir. Installer
            # senaryosunda uygulama zaten kapali olmalidir (CHECK_APP_RUNNING);
            # bu dosyalarin varligi beklenmeyen bir durumdur -> fail-closed.
            return False, f"{os.path.basename(path)}{ek} mevcut — checkpoint edilmemis olabilir"
    try:
        con = sqlite3.connect(_ro(path), uri=True)
    except sqlite3.Error as exc:
        return False, f"acilamiyor: {type(exc).__name__}"
    try:
        cur = con.cursor()
        try:
            butunluk = cur.execute("PRAGMA integrity_check").fetchone()[0]
        except sqlite3.DatabaseError:
            return False, "SQLite dosyasi degil / bozuk"
        if butunluk != "ok":
            return False, f"integrity_check={butunluk}"
        if cur.execute("PRAGMA foreign_key_check").fetchall():
            return False, "foreign_key_check ihlalleri var"
        tablolar = {
            r[0] for r in cur.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"
            ).fetchall()
        }
        cekirdek = {"customers", "offers", "incidents", "alembic_version"} | policy.KNOWN_LEGACY_ONLY_TABLES
        if not cekirdek.issubset(tablolar):
            return False, f"beklenen cekirdek tablolar eksik: {sorted(cekirdek - tablolar)}"
        return True, "ok"
    finally:
        con.close()


def classify_precedence(legacy_path: str, canonical_path: str) -> PrecedenceClassification:
    """
    Yedi durumlu oncelik politikasini SALT-OKUNUR olarak degerlendirir.

    Cagrildigi yerler:
    - perform_rescue() [PDSMR-R2]
    - tests/test_legacy_adoption_rescue.py
    """
    legacy_var = os.path.isfile(legacy_path)
    canonical_var = os.path.isfile(canonical_path)

    if not legacy_var and not canonical_var:
        return PrecedenceClassification(PrecedenceState.FRESH_INSTALL, "ikisi de yok")

    if legacy_var and not canonical_var:
        gecerli, sebep = _is_readable_family_db(legacy_path)
        if not gecerli:
            return PrecedenceClassification(PrecedenceState.LEGACY_INVALID, sebep)
        return PrecedenceClassification(
            PrecedenceState.LEGACY_ELIGIBLE, "kurtarilabilir", legacy_sha256=_sha256(legacy_path)
        )

    if not legacy_var and canonical_var:
        gecerli, sebep = _is_readable_family_db(canonical_path)
        if not gecerli:
            return PrecedenceClassification(PrecedenceState.CANONICAL_INVALID, sebep)
        return PrecedenceClassification(
            PrecedenceState.CANONICAL_ALREADY_PRESENT, "canonical zaten var",
            canonical_sha256=_sha256(canonical_path),
        )

    # ikisi de var
    legacy_ok, legacy_sebep = _is_readable_family_db(legacy_path)
    if not legacy_ok:
        return PrecedenceClassification(PrecedenceState.LEGACY_INVALID, legacy_sebep)
    canonical_ok, canonical_sebep = _is_readable_family_db(canonical_path)
    if not canonical_ok:
        return PrecedenceClassification(PrecedenceState.CANONICAL_INVALID, canonical_sebep)

    l_hash, c_hash = _sha256(legacy_path), _sha256(canonical_path)
    if l_hash == c_hash:
        return PrecedenceClassification(
            PrecedenceState.EQUIVALENT, "hash esit", legacy_sha256=l_hash, canonical_sha256=c_hash
        )
    return PrecedenceClassification(
        PrecedenceState.CONFLICT, "hash farkli — ikisi de korunuyor",
        legacy_sha256=l_hash, canonical_sha256=c_hash,
    )


def _fault(point: str, fault_at: Optional[str]) -> None:
    if fault_at == point:
        raise InjectedFault(f"enjekte edilmis kesinti: {point}")


def _journal_path(canonical_path: str) -> str:
    return canonical_path + JOURNAL_SUFFIX


def _temp_path(canonical_path: str) -> str:
    # SABIT isim: yeniden calistirma eski temp'in USTUNE yazar, cogalmaz.
    return canonical_path + ".rescue-tmp"


def read_journal(canonical_path: str) -> Optional[dict]:
    """Journal'i okur ve imzasini dogrular. Bozuksa/eksikse None doner."""
    yol = _journal_path(canonical_path)
    if not os.path.isfile(yol):
        return None
    try:
        with open(yol, encoding="utf-8") as fh:
            paket = json.load(fh)
        beklenen = hashlib.sha256(
            json.dumps(paket["journal"], indent=1, ensure_ascii=False, sort_keys=True)
            .encode("utf-8")
        ).hexdigest()
        if beklenen != paket.get("journal_sha256"):
            return None
        return paket["journal"]
    except (OSError, ValueError, KeyError):
        return None


def _write_journal(canonical_path: str, payload: dict) -> None:
    metin = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True)
    imza = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    with open(_journal_path(canonical_path), "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(
            {"journal": payload, "journal_sha256": imza, "journal_version": JOURNAL_VERSION},
            indent=1, ensure_ascii=False, sort_keys=True,
        ))


def perform_rescue(
    legacy_path: str,
    canonical_path: str,
    backups_dir: str,
    *,
    version_label: str,
    confirm_installer_context: bool = False,
    fault_at: Optional[str] = None,
) -> RescueReport:
    """
    Installer temizliginden ONCE legacy DB'yi canonical konuma kurtarir.

    Args:
        legacy_path: `resources/backend/gelka_enerji.db` (installer'in
            SILECEGI dizindeki dosya). ASLA silinmez/degistirilmez.
        canonical_path: `<userData>/database/gelka_enerji.db`.
        backups_dir: `<userData>/database/backups/`. Rollback yedegi buraya
            versiyonlanmis, degismez bir adla yazilir.
        version_label: Yedek dosya adina gomulecek surum etiketi (ör. "1.0.6").
            Bosluk/ozel karakter icermemelidir — dosya adi guvenligi icin
            cagiran taraf temizler.
        confirm_installer_context: Cagiranin acik onayi.
        fault_at: YALNIZ test — belirtilen noktada kesinti enjekte eder.

    Raises:
        RescueRefused: on-kosul saglanmadi / HARD_STOP durumu.
        InjectedFault: test kesintisi.

    Cagrildigi yerler:
    - tests/test_legacy_adoption_rescue.py [PDSMR-R2]
    """
    if not confirm_installer_context:
        raise RescueRefused("confirm_installer_context=True verilmedi")
    if fault_at is not None and fault_at not in FAULT_POINTS:
        raise RescueRefused(f"bilinmeyen fault noktasi: {fault_at}")

    for etiket, yol in (("canonical", canonical_path), ("backups_dir", backups_dir)):
        marker = is_forbidden_target(yol)
        if marker:
            raise RescueRefused(f"{etiket} kurulu uygulama alaninda (marker={marker!r})")
    if same_file(legacy_path, canonical_path):
        raise RescueRefused("legacy ile canonical AYNI dosya — mutasyon reddedildi")

    siniflandirma = classify_precedence(legacy_path, canonical_path)

    if siniflandirma.state is PrecedenceState.FRESH_INSTALL:
        return RescueReport(outcome="NOOP_FRESH_INSTALL", precedence=siniflandirma.state,
                             canonical_path=canonical_path, details={"detail": siniflandirma.detail})
    if siniflandirma.state is PrecedenceState.CANONICAL_ALREADY_PRESENT:
        return RescueReport(outcome="NOOP_CANONICAL_PRESENT", precedence=siniflandirma.state,
                             canonical_path=canonical_path, details={"detail": siniflandirma.detail})
    if siniflandirma.state is PrecedenceState.EQUIVALENT:
        return RescueReport(outcome="NOOP_EQUIVALENT", precedence=siniflandirma.state,
                             canonical_path=canonical_path, source_sha256=siniflandirma.legacy_sha256 or "",
                             details={"detail": siniflandirma.detail})
    if siniflandirma.state in _HARD_STOP_STATES:
        raise RescueRefused(f"{siniflandirma.state.value}: {siniflandirma.detail}")

    # ── A: kurtarilabilir — asil transaction ────────────────────────────
    assert siniflandirma.state is PrecedenceState.LEGACY_ELIGIBLE
    kaynak_hash = siniflandirma.legacy_sha256 or _sha256(legacy_path)

    canonical_dir = os.path.dirname(os.path.abspath(canonical_path))
    if not os.path.isdir(canonical_dir):
        os.makedirs(canonical_dir, exist_ok=True)
    if not os.path.isdir(backups_dir):
        os.makedirs(backups_dir, exist_ok=True)

    guvenli_etiket = "".join(c for c in version_label if c.isalnum() or c in ".-_") or "unknown"
    backup_path = os.path.join(
        backups_dir, f"gelka_enerji.pre-upgrade.{guvenli_etiket}.{kaynak_hash[:12]}.db"
    )
    if same_file(legacy_path, backup_path):
        raise RescueRefused("hesaplanan yedek yolu legacy ile CAKISIYOR")

    temp_path = _temp_path(canonical_path)
    try:
        # 1-3) ro ac + aile + butunluk zaten classify_precedence icinde dogrulandi.
        # 4) rollback yedegi (duz byte kopyasi — kaynaga YAZMAZ, sadece OKUR)
        _fault("before_backup", fault_at)
        if not (os.path.isfile(backup_path) and _sha256(backup_path) == kaynak_hash):
            gecici_yedek = backup_path + ".partial"
            _copy_bytes_durable(legacy_path, gecici_yedek)
            os.replace(gecici_yedek, backup_path)
        # 5) yedek parmak izi dogrulamasi
        if _sha256(backup_path) != kaynak_hash:
            raise RescueRefused("YEDEK parmak izi kaynakla eslesmiyor — kurtarma durduruldu")
        _fault("after_backup", fault_at)

        # 6) temp'e kopyala (canonical HEDEF dosya sisteminde — cross-fs rename riski yok)
        _fault("before_temp_copy", fault_at)
        if os.path.exists(temp_path):
            os.remove(temp_path)  # stale temp file: onceki yarim denemeden kalmis olabilir
        _copy_bytes_durable(legacy_path, temp_path)  # 7) fsync _copy_bytes_durable icinde
        _fault("after_temp_copy", fault_at)

        # 8) temp dogrulamasi
        if _sha256(temp_path) != kaynak_hash:
            raise RescueRefused("TEMP kopya parmak izi kaynakla eslesmiyor — rename YAPILMADI")
        temp_con = sqlite3.connect(_ro(temp_path), uri=True)
        try:
            butunluk = temp_con.execute("PRAGMA integrity_check").fetchone()[0]
            fk = len(temp_con.execute("PRAGMA foreign_key_check").fetchall())
        finally:
            temp_con.close()
        if butunluk != "ok" or fk:
            raise RescueRefused(f"TEMP kopya bozuk: integrity={butunluk} fk={fk} — rename YAPILMADI")
        # source runtime'da degisti mi (savunma amacli ikinci kontrol)
        if _sha256(legacy_path) != kaynak_hash:
            raise RescueRefused("KAYNAK kopyalama sirasinda DEGISTI — kurtarma durduruldu")

        # 9) atomik rename — hedef TOCTOU'ya karsi tekrar kontrol edilir
        _fault("before_rename", fault_at)
        if os.path.exists(canonical_path):
            raise RescueRefused(
                "canonical hedef kurtarma SIRASINDA olustu — cakisma, rename yapilmadi"
            )
        os.rename(temp_path, canonical_path)  # Windows: hedef varsa FileExistsError (OSError)
    except InjectedFault:
        raise
    except RescueRefused:
        raise
    except OSError as exc:
        # disk-full / access-denied / antivirus lock / hedef kurtarma
        # sirasinda dolmus (FileExistsError) — hepsi ayni fail-closed yol.
        raise RescueRefused(f"dosya sistemi hatasi: {type(exc).__name__}: {exc}") from exc
    finally:
        if os.path.exists(temp_path):
            try:
                os.remove(temp_path)  # best-effort temizlik; basarili rename sonrasi zaten yok
            except OSError:
                pass
    _fault("after_rename", fault_at)

    # 10) canonical'i tekrar ac + dogrula
    if _sha256(canonical_path) != kaynak_hash:
        raise RescueRefused("CANONICAL rename sonrasi parmak izi eslesmiyor")
    canon_con = sqlite3.connect(_ro(canonical_path), uri=True)
    try:
        butunluk2 = canon_con.execute("PRAGMA integrity_check").fetchone()[0]
        fk2 = len(canon_con.execute("PRAGMA foreign_key_check").fetchall())
    finally:
        canon_con.close()
    if butunluk2 != "ok" or fk2:
        raise RescueRefused(f"CANONICAL dogrulamasi basarisiz: integrity={butunluk2} fk={fk2}")

    # 11) DB DISINDA imzali journal (musteri verisi / secret YOK)
    _fault("before_journal", fault_at)
    _write_journal(canonical_path, {
        "outcome": "RESCUED",
        "legacy_path_basename": os.path.basename(legacy_path),
        "canonical_path": canonical_path,
        "backup_path": backup_path,
        "source_sha256": kaynak_hash,
        "version_label": guvenli_etiket,
        "integrity_check": butunluk2,
        "foreign_key_violations": fk2,
    })

    # Legacy KAYNAK ASLA silinmez/degistirilmez.
    return RescueReport(
        outcome="RESCUED", precedence=siniflandirma.state, canonical_path=canonical_path,
        backup_path=backup_path, source_sha256=kaynak_hash,
        integrity_check=butunluk2, foreign_key_violations=fk2,
        details={"legacy_untouched": True},
    )


__all__ = [
    "FAULT_POINTS",
    "JOURNAL_SUFFIX",
    "InjectedFault",
    "PrecedenceClassification",
    "PrecedenceState",
    "RescueRefused",
    "RescueReport",
    "classify_precedence",
    "perform_rescue",
    "read_journal",
]


# ── CLI (gelecekte PyInstaller ile ayri bir rescue.exe uretmek icin) ────
# Bu turda GERCEK derleme YAPILMAZ (installer execution yetkisiz); CLI
# yalniz arayuzu sabitler ki electron/build/installer.nsh onu cagirabilsin.
def _main() -> int:  # pragma: no cover - manuel/CLI yolu, testler dogrudan API kullanir
    import argparse
    import sys

    ayristirici = argparse.ArgumentParser(description="PDSMR-R2 installer pre-upgrade rescue")
    ayristirici.add_argument("--legacy", required=True)
    ayristirici.add_argument("--canonical", required=True)
    ayristirici.add_argument("--backups-dir", required=True)
    ayristirici.add_argument("--version-label", required=True)
    args = ayristirici.parse_args()

    try:
        rapor = perform_rescue(
            args.legacy, args.canonical, args.backups_dir,
            version_label=args.version_label, confirm_installer_context=True,
        )
    except RescueRefused as exc:
        sys.stderr.write(f"RESCUE_REFUSED: {exc}\n")
        return 1
    sys.stdout.write(f"RESCUE_OK: {rapor.outcome}\n")
    return 0


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(_main())
