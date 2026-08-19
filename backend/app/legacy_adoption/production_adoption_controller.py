"""
PDSMR-R4 / FAZ 4C1 — PRODUCTION ADOPTION CONTROLLER (fail-closed operator yolu).

Faz 4B2'de dogrulanan kontrollu adoption MOTORU (unversioned_adoption.py)
uzerine, production'a OZEL operator katmani. Motor DEGISTIRILMEZ; bu modul
yalnizca ONUN etrafina kapi/yetki/yedek/geri-alma sozlesmesi kurar.

NEDEN AYRI BIR KATMAN: 4B2 motoru bilincli olarak DISPOSABLE hedeflere
kilitlidir (`assert_disposable_target` kurulu uygulama alanini reddeder).
Gercek production adoption bu kilidi ACMAK zorundadir — ama GENEL bir
bypass ile DEGIL. Burada acilis TEK KULLANIMLIK, exact production
parmak izine + exact realpath'e + exact repository SHA'sina BAGLI bir
authorization manifest'ine baglanir. Manifest yoksa/bayatsa/tekrar
kullanilmissa/baska hedefe aitse: DETERMINISTIK RET.

FAZ 4C1 SINIRI: bu modul gelistirilir ve YALNIZ disposable kopyalarda
prova edilir. Gercek production manifest'i URETILMEZ ve production
execution modu CAGRILMAZ — o Faz 4C2'nin isidir ve AYRI owner GO gerektirir.

YASAKLAR (kod duzeyinde zorlanir):
  - ad-hoc SQL / elle DDL / create_all / normal alembic upgrade / ara stamp
  - production'a yazan baglanti (yalniz SQLite URI mode=ro dogrulama)
  - manifest'siz production yoluna yazma
  - basarisiz post-publish sertifikasyonundan sonra "basarili" iddiasi
  - audit'in terminal basari ONCESI yazilmasi

Cagrildigi yerler:
- tests/test_pdsmr_r4c1_production_controller.py [PDSMR-R4/Faz4C1]
  (Bilincli olarak HICBIR router/CLI/startup/installer yolundan cagrilmaz.)
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import sqlite3
import subprocess
from dataclasses import dataclass, field
from typing import Any, Optional

from .pathsafety import is_forbidden_target, real_path, same_file
from .unversioned_adoption import (
    AdoptionRefused,
    InjectedFault,
    adopt_unversioned_copy,
    certify_canonical_equivalence,
    build_canonical_reference,
)
from .lineage import CANONICAL_HEAD

AUDIT_SUFFIX = ".pdsmr-r4c1-cutover-audit.json"
AUDIT_VERSION = "PDSMR-R4C1/1"
NONCE_LEDGER_SUFFIX = ".pdsmr-r4c1-consumed-nonces.json"

# Controller katmanina OZGU kesinti noktalari. 4B2'nin 50 noktasi
# DEGISMEDEN korunur; bunlar onun USTUNE eklenir.
CONTROLLER_FAULT_POINTS = (
    "before_identity_binding", "after_identity_binding",
    "before_backup_copy", "after_backup_copy",
    "before_backup_verification", "after_backup_verification",
    "before_working_adoption", "after_working_adoption",
    "before_atomic_publish", "after_atomic_publish",
    "during_post_publish_certification",
    "before_rollback_replace", "after_rollback_replace",
    "during_rollback_certification",
    "before_audit_commit", "after_audit_commit",
    "before_authorization_consume", "after_authorization_consume",
    "lost_response_retry",
)


class ControllerRefused(Exception):
    """Kapi gecilemedi. Hedefe ANLAMLI yazma YAPILMADI (veya geri alindi)."""


class RecoveryState(str):
    """Basarisizlik aninda hedefin DURUMU — asla belirsiz birakilmaz."""


RECOVERY_UNTOUCHED = "TARGET_UNTOUCHED"
RECOVERY_RESTORED = "TARGET_RESTORED_FROM_BACKUP"
RECOVERY_MANUAL_REQUIRED = "MANUAL_RECOVERY_REQUIRED"


@dataclass(frozen=True)
class AuthorizationManifest:
    """
    TEK KULLANIMLIK production yetkisi.

    Dort seye AYNI ANDA baglidir: nonce, repository SHA, hedefin exact
    realpath'i ve hedefin exact parmak izi (sha256 + boyut). Bunlardan
    biri bile uymazsa yetki GECERSIZDIR — replay, bayat manifest ve
    "baska hedefe ait" manifest yapisal olarak reddedilir.
    """

    nonce: str
    repository_sha: str
    target_realpath: str
    target_sha256: str
    target_size: int
    issued_at_utc: str

    def to_dict(self) -> dict[str, Any]:
        return {
            "nonce": self.nonce,
            "repository_sha": self.repository_sha,
            "target_realpath": self.target_realpath,
            "target_sha256": self.target_sha256,
            "target_size": self.target_size,
            "issued_at_utc": self.issued_at_utc,
        }


@dataclass
class CutoverReport:
    outcome: str  # "REHEARSED" | "ADOPTED" | "ROLLED_BACK"
    mode: str  # "REHEARSAL" | "PRODUCTION"
    recovery_state: str = RECOVERY_UNTOUCHED
    target_realpath: str = ""
    target_sha256_before: str = ""
    target_sha256_after: str = ""
    backup_path: str = ""
    backup_sha256: str = ""
    terminal_revision: str = ""
    integrity_check: str = ""
    foreign_key_violations: int = -1
    row_counts_before: dict[str, int] = field(default_factory=dict)
    row_counts_after: dict[str, int] = field(default_factory=dict)
    gates_passed: tuple[str, ...] = ()
    accepted_data_variants: tuple[str, ...] = ()
    authorization_nonce: str = ""
    details: dict[str, Any] = field(default_factory=dict)


# ── Temel yardimcilar (salt-okunur) ─────────────────────────────────────
def sha256_of(path: str) -> str:
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for parca in iter(lambda: fh.read(1 << 20), b""):
            h.update(parca)
    return h.hexdigest()


def _ro(path: str) -> str:
    return "file:" + path.replace("\\", "/").replace(" ", "%20") + "?mode=ro"


def _fault(point: str, fault_at: Optional[str]) -> None:
    if fault_at == point:
        raise InjectedFault("enjekte edilmis kesinti: " + point)


def timestamp_fields(path: str) -> dict[str, Any]:
    """
    Timestamp'i BIRIMI ACIK sekilde kaydeder.

    NEDEN: onceki fazlarda `stat -c %Y` (SANIYE) yanlislikla "mtime_ns"
    olarak etiketlenmisti. Deger dogruydu, ETIKET yanlisti. Burada her iki
    alan da AYRI AYRI ve birimiyle kaydedilir; ayrica UTC ISO-8601
    karsiligi verilir ki birim belirsizligi bir daha olusamasin.

    Cagrildigi yerler:
    - verify_production_identity() [PDSMR-R4/Faz4C1]
    - tests/test_pdsmr_r4c1_production_controller.py
    """
    import datetime

    st = os.stat(path)
    return {
        "st_mtime_seconds": st.st_mtime,
        "st_mtime_ns": st.st_mtime_ns,
        "mtime_utc_iso8601": datetime.datetime.fromtimestamp(
            st.st_mtime, datetime.timezone.utc
        ).isoformat(),
        "size_bytes": st.st_size,
    }


def row_manifest(path: str) -> dict[str, int]:
    """Tum uygulama tablolarinin satir sayisi (salt-okunur)."""
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        tablolar = sorted(r[0] for r in con.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'"))
        return {t: con.execute('SELECT COUNT(*) FROM "' + t + '"').fetchone()[0]
                for t in tablolar}
    finally:
        con.close()


def health(path: str) -> tuple[str, int]:
    con = sqlite3.connect(_ro(path), uri=True)
    try:
        return (con.execute("PRAGMA integrity_check").fetchone()[0],
                len(con.execute("PRAGMA foreign_key_check").fetchall()))
    finally:
        con.close()


# ── KAPI 1-11: on-kosul kapilari ────────────────────────────────────────
def running_gelka_processes() -> list[dict[str, str]]:
    """
    Kurulu uygulama/backend sureclerini EXACT path + PID + command line ile
    tespit eder.

    ISIM BAZLI toplu kapatma (taskkill/Stop-Process) YAPILMAZ ve
    YAPILMAMALIDIR (onceki olay dersi). Bu fonksiyon yalniz TESPIT eder;
    surec acikssa cagiran HARD_STOP verir ve owner'in kapatmasini ister.

    Cagrildigi yerler:
    - preflight_gates() [PDSMR-R4/Faz4C1]
    - tests/test_pdsmr_r4c1_production_controller.py
    """
    ps = (
        "Get-CimInstance Win32_Process | "
        "Where-Object { $_.ExecutablePath -like '*Programs\\Gelka*' } | "
        "ForEach-Object { '{0}|{1}' -f $_.ProcessId, $_.ExecutablePath }"
    )
    try:
        cikti = subprocess.run(
            ["powershell", "-NoProfile", "-Command", ps],
            capture_output=True, text=True, timeout=60,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return []
    sonuc = []
    for satir in cikti.strip().splitlines():
        if "|" in satir:
            pid, _, yol = satir.partition("|")
            sonuc.append({"pid": pid.strip(), "path": yol.strip()})
    return sonuc


def verify_production_identity(
    target: str, *, expected_sha256: str, expected_size: int,
) -> dict[str, Any]:
    """
    KAPI 2-5: exact realpath, exact parmak izi, birimi acik timestamp,
    sidecar yoklugu.

    Yol VARSAYILMAZ: cagiran exact yolu verir; burada realpath ile
    cozulur ve symlink/junction/reparse/case-alias sapmasi REDDEDILIR.

    Cagrildigi yerler:
    - preflight_gates() [PDSMR-R4/Faz4C1]
    - tests/test_pdsmr_r4c1_production_controller.py
    """
    if not os.path.isfile(target):
        raise ControllerRefused("hedef dosya yok: " + target)

    mutlak = os.path.abspath(target)
    gercek = os.path.realpath(mutlak)
    if os.path.normcase(gercek) != os.path.normcase(mutlak):
        raise ControllerRefused(
            "hedef bir symlink/junction/reparse noktasi — belirsiz kimlik: "
            + repr(mutlak) + " -> " + repr(gercek)
        )

    for ek in ("-wal", "-shm", "-journal"):
        if os.path.exists(target + ek):
            raise ControllerRefused("SQLite sidecar mevcut: " + ek + " — uygulama kapali degil?")

    zaman = timestamp_fields(target)
    if zaman["size_bytes"] != expected_size:
        raise ControllerRefused(
            "boyut sapmasi: " + str(zaman["size_bytes"]) + " != " + str(expected_size))
    gercek_hash = sha256_of(target)
    if gercek_hash != expected_sha256:
        raise ControllerRefused("SHA-256 sapmasi — hedef beklenen DB DEGIL")

    butunluk, fk = health(target)
    if butunluk != "ok":
        raise ControllerRefused("integrity_check=" + butunluk)
    if fk:
        raise ControllerRefused(str(fk) + " FK ihlali")

    return {
        "realpath": gercek,
        "sha256": gercek_hash,
        **zaman,
        "integrity_check": butunluk,
        "foreign_key_violations": fk,
    }


def assert_same_volume_and_atomic_replace_possible(a: str, b: str) -> None:
    """
    KAPI 9: atomic replace ANCAK ayni volume'de garanti edilir.
    Farkli volume'de `os.replace` kopyalamaya duser ve atomiklik KAYBOLUR.
    """
    va = os.path.splitdrive(os.path.abspath(a))[0].lower()
    vb = os.path.splitdrive(os.path.abspath(b))[0].lower()
    if va != vb:
        raise ControllerRefused(
            "atomic replace garanti edilemez: farkli volume " + repr(va) + " vs " + repr(vb))


def assert_sufficient_disk_space(hedef_dizin: str, gerekli_bayt: int, *, kat: int = 3) -> None:
    """KAPI 10: yedek + calisma kopyasi + pay icin yeterli alan."""
    try:
        serbest = shutil.disk_usage(hedef_dizin).free
    except OSError as exc:
        raise ControllerRefused("disk alani olculemedi: " + str(exc)) from exc
    gerekli = gerekli_bayt * kat
    if serbest < gerekli:
        raise ControllerRefused(
            "yetersiz disk alani: serbest=" + str(serbest) + " gerekli=" + str(gerekli))


def assert_physically_distinct(**yollar: str) -> None:
    """KAPI 8: SOURCE/ROLLBACK/WORKING/target FIZIKSEL olarak ayri olmali."""
    adlar = sorted(yollar)
    for i, a in enumerate(adlar):
        for b in adlar[i + 1:]:
            if same_file(yollar[a], yollar[b]) or real_path(yollar[a]) == real_path(yollar[b]):
                raise ControllerRefused(a + " ve " + b + " AYNI fiziksel dosya")


# ── Authorization manifest (tek kullanimlik) ────────────────────────────
def issue_authorization(
    target: str, *, repository_sha: str, nonce: str, issued_at_utc: str,
    confirm_production_authorization: bool = False,
) -> AuthorizationManifest:
    """
    Hedefe BAGLI tek kullanimlik yetki uretir.

    FAZ 4C1'de gercek production hedefi icin CAGRILMAZ — yalniz disposable
    prova hedefleri icin. Gercek production manifest'i Faz 4C2'nin ve ayri
    owner GO'sunun konusudur.

    Cagrildigi yerler:
    - tests/test_pdsmr_r4c1_production_controller.py [PDSMR-R4/Faz4C1]
    """
    if not confirm_production_authorization:
        raise ControllerRefused("confirm_production_authorization=True verilmedi")
    if not nonce or len(nonce) < 16:
        raise ControllerRefused("nonce en az 16 karakter olmali")
    if not os.path.isfile(target):
        raise ControllerRefused("yetki verilecek hedef yok")
    return AuthorizationManifest(
        nonce=nonce,
        repository_sha=repository_sha,
        target_realpath=real_path(target),
        target_sha256=sha256_of(target),
        target_size=os.path.getsize(target),
        issued_at_utc=issued_at_utc,
    )


def _ledger_path(ledger_dir: str) -> str:
    return os.path.join(ledger_dir, "nonces" + NONCE_LEDGER_SUFFIX)


def _consumed_nonces(ledger_dir: str) -> set[str]:
    yol = _ledger_path(ledger_dir)
    if not os.path.isfile(yol):
        return set()
    try:
        with open(yol, encoding="utf-8") as fh:
            return set(json.load(fh).get("consumed", []))
    except (OSError, ValueError):
        # Bozuk defter -> fail-closed: hicbir nonce guvenle kullanilamaz.
        raise ControllerRefused("nonce defteri okunamadi/bozuk — fail-closed")


def _consume_nonce(ledger_dir: str, nonce: str) -> None:
    os.makedirs(ledger_dir, exist_ok=True)
    mevcut = _consumed_nonces(ledger_dir)
    if nonce in mevcut:
        raise ControllerRefused("nonce ZATEN TUKETILMIS (replay) — reddedildi")
    mevcut.add(nonce)
    gecici = _ledger_path(ledger_dir) + ".tmp"
    with open(gecici, "w", encoding="utf-8", newline="\n") as fh:
        json.dump({"consumed": sorted(mevcut)}, fh, indent=1)
        fh.flush()
        os.fsync(fh.fileno())
    os.replace(gecici, _ledger_path(ledger_dir))


def validate_authorization(
    manifest: AuthorizationManifest, target: str, *, repository_sha: str, ledger_dir: str,
) -> None:
    """
    Yetkinin GECERLI, TAZE ve BU hedefe ait oldugunu kanitlar.

    Dort baglama da AYRI AYRI dogrulanir; biri bile uymazsa ret.
    Tuketilmis nonce (replay) ayrica reddedilir.

    Cagrildigi yerler:
    - run_cutover() [PDSMR-R4/Faz4C1]
    - tests/test_pdsmr_r4c1_production_controller.py
    """
    if manifest.repository_sha != repository_sha:
        raise ControllerRefused(
            "yetki BASKA repository SHA'sina ait: " + manifest.repository_sha
            + " != " + repository_sha)
    if manifest.target_realpath != real_path(target):
        raise ControllerRefused("yetki BASKA hedefe ait — realpath uyusmuyor")
    if manifest.target_size != os.path.getsize(target):
        raise ControllerRefused("yetki BAYAT — hedef boyutu degismis")
    if manifest.target_sha256 != sha256_of(target):
        raise ControllerRefused("yetki BAYAT — hedef parmak izi degismis")
    if manifest.nonce in _consumed_nonces(ledger_dir):
        raise ControllerRefused("nonce ZATEN TUKETILMIS (replay) — reddedildi")


# ── KAPI 11: dogrulanmis degismez yedek ─────────────────────────────────
def create_verified_backup(target: str, recovery_dir: str, *, etiket: str) -> dict[str, Any]:
    """
    Uygulama KAPALIYKEN alinan, byte-identical, ayri ve dogrulanmis yedek.

    Yedek AYRI bir recovery yolunda tutulur ve evidence icine KOPYALANMAZ
    (owner karari) — evidence yalniz hash/boyut/timestamp/realpath KAYDINI
    icerir, DB'nin kendisini DEGIL.

    Cagrildigi yerler:
    - run_cutover() [PDSMR-R4/Faz4C1]
    - tests/test_pdsmr_r4c1_production_controller.py
    """
    if running_gelka_processes():
        raise ControllerRefused("yedek alinamaz: GELKA surecleri acik")
    os.makedirs(recovery_dir, exist_ok=True)
    kaynak_hash = sha256_of(target)
    yedek = os.path.join(
        recovery_dir, "gelka_enerji.pre-cutover." + etiket + "." + kaynak_hash[:12] + ".db")
    assert_same_volume_and_atomic_replace_possible(target, recovery_dir)
    assert_sufficient_disk_space(recovery_dir, os.path.getsize(target))

    gecici = yedek + ".partial"
    with open(target, "rb") as k, open(gecici, "wb") as h:
        shutil.copyfileobj(k, h, length=1 << 20)
        h.flush()
        os.fsync(h.fileno())
    os.replace(gecici, yedek)

    if sha256_of(yedek) != kaynak_hash:
        raise ControllerRefused("YEDEK byte-identical DEGIL — cutover durduruldu")
    butunluk, fk = health(yedek)
    if butunluk != "ok" or fk:
        raise ControllerRefused("yedek dogrulamasi basarisiz: integrity=" + butunluk)
    return {
        "backup_path": yedek,
        "backup_sha256": kaynak_hash,
        "source_realpath": real_path(target),
        **timestamp_fields(yedek),
    }


def _atomic_restore(backup: str, target: str, beklenen_hash: str) -> str:
    """
    Yedegi hedefe ATOMIK olarak geri koyar ve SONUCU DOGRULAR.

    Geri koyma dogrulanamazsa "basarili" DENMEZ — MANUAL_RECOVERY_REQUIRED
    dondurulur ve cagiran bunu acikca raporlar.
    """
    try:
        gecici = target + ".r4c1-restore.tmp"
        with open(backup, "rb") as k, open(gecici, "wb") as h:
            shutil.copyfileobj(k, h, length=1 << 20)
            h.flush()
            os.fsync(h.fileno())
        os.replace(gecici, target)
    except OSError:
        return RECOVERY_MANUAL_REQUIRED
    try:
        if sha256_of(target) != beklenen_hash:
            return RECOVERY_MANUAL_REQUIRED
        butunluk, fk = health(target)
        if butunluk != "ok" or fk:
            return RECOVERY_MANUAL_REQUIRED
    except (OSError, sqlite3.DatabaseError):
        return RECOVERY_MANUAL_REQUIRED
    return RECOVERY_RESTORED


# ── Audit (DB DISINDA, yalniz TERMINAL BASARIDAN SONRA) ─────────────────
def _write_audit(target: str, payload: dict) -> None:
    metin = json.dumps(payload, indent=1, ensure_ascii=False, sort_keys=True)
    imza = hashlib.sha256(metin.encode("utf-8")).hexdigest()
    with open(target + AUDIT_SUFFIX, "w", encoding="utf-8", newline="\n") as fh:
        fh.write(json.dumps(
            {"audit": payload, "audit_sha256": imza, "audit_version": AUDIT_VERSION},
            indent=1, ensure_ascii=False, sort_keys=True))


def read_audit(target: str) -> Optional[dict]:
    yol = target + AUDIT_SUFFIX
    if not os.path.isfile(yol):
        return None
    try:
        with open(yol, encoding="utf-8") as fh:
            paket = json.load(fh)
        beklenen = hashlib.sha256(json.dumps(
            paket["audit"], indent=1, ensure_ascii=False, sort_keys=True
        ).encode("utf-8")).hexdigest()
        return paket["audit"] if beklenen == paket.get("audit_sha256") else None
    except (OSError, ValueError, KeyError):
        return None


# ── ANA AKIS ────────────────────────────────────────────────────────────
def run_cutover(
    target: str,
    *,
    source_path: str,
    rollback_path: str,
    working_path: str,
    recovery_dir: str,
    scratch_dir: str,
    ledger_dir: str,
    expected_sha256: str,
    expected_size: int,
    repository_sha: str,
    authorization: Optional[AuthorizationManifest] = None,
    version_label: str = "unknown",
    confirm_disposable_rehearsal: bool = False,
    fault_at: Optional[str] = None,
) -> CutoverReport:
    """
    Uctan uca cutover: kimlik -> yedek -> adoption -> atomik yayim ->
    publish sonrasi sertifikasyon -> gerekirse DOGRULANMIS atomik geri alma.

    MOD SECIMI (genel bypass YOK):
      - `authorization` VERILMEDIYSE: hedef DISPOSABLE olmak ZORUNDA
        (kurulu uygulama alani REDDEDILIR). Mod = REHEARSAL.
      - `authorization` VERILDIYSE: yetki dort baglamin HEPSINDE dogrulanir
        (repo SHA, exact realpath, exact sha256, exact boyut) ve nonce TEK
        KULLANIMLIKTIR. Mod = PRODUCTION.

    FAZ 4C1: yalniz REHEARSAL modu ve disposable hedefler kullanilir.

    Raises:
        ControllerRefused: kapi gecilemedi.
        InjectedFault: test kesintisi.

    Cagrildigi yerler:
    - tests/test_pdsmr_r4c1_production_controller.py [PDSMR-R4/Faz4C1]
    """
    if fault_at is not None and fault_at not in CONTROLLER_FAULT_POINTS:
        raise ControllerRefused("bilinmeyen controller fault noktasi: " + str(fault_at))

    mod = "PRODUCTION" if authorization is not None else "REHEARSAL"
    gecilen: list[str] = []

    # ── KAPI 1: calisma ortami kimligi ─────────────────────────────────
    if mod == "REHEARSAL" and not confirm_disposable_rehearsal:
        raise ControllerRefused("confirm_disposable_rehearsal=True verilmedi")

    # ── KAPI 6: surec yoklugu (ISIM BAZLI KILL YOK — yalniz tespit) ────
    _fault("before_identity_binding", fault_at)
    surecler = running_gelka_processes()
    if surecler:
        raise ControllerRefused(
            "GELKA surecleri ACIK (owner kapatmali, otomatik kapatma YAPILMAZ): "
            + str([s["pid"] for s in surecler]))
    gecilen.append("process_absence")

    # ── KAPI 2-5: kimlik + parmak izi + timestamp + sidecar ────────────
    kimlik = verify_production_identity(
        target, expected_sha256=expected_sha256, expected_size=expected_size)
    gecilen.extend(["exact_realpath", "fingerprint", "timestamp_units", "sidecar_absence"])

    # ── KAPI 7: hedef alani politikasi (GENEL BYPASS YOK) ──────────────
    yasak = is_forbidden_target(target)
    if authorization is None:
        if yasak:
            raise ControllerRefused(
                "hedef kurulu uygulama alaninda (marker=" + repr(yasak) + ") ve YETKI YOK — "
                "production execution icin tek kullanimlik authorization manifest ZORUNLU")
    else:
        validate_authorization(
            authorization, target, repository_sha=repository_sha, ledger_dir=ledger_dir)
        gecilen.append("authorization_binding")
    _fault("after_identity_binding", fault_at)

    # ── KAPI 8-10: fiziksel ayrilik + volume + disk ────────────────────
    assert_physically_distinct(
        target=target, source=source_path, rollback=rollback_path, working=working_path)
    assert_same_volume_and_atomic_replace_possible(working_path, target)
    assert_sufficient_disk_space(os.path.dirname(os.path.abspath(target)),
                                 os.path.getsize(target))
    gecilen.extend(["physical_distinctness", "same_volume_atomic", "disk_space"])

    if sha256_of(source_path) != expected_sha256:
        raise ControllerRefused("SOURCE parmak izi beklenenden farkli")
    if sha256_of(rollback_path) != expected_sha256:
        raise ControllerRefused("ROLLBACK, SOURCE ile byte-identical degil")

    satir_once = row_manifest(target)

    # ── KAPI 11: dogrulanmis degismez yedek ────────────────────────────
    _fault("before_backup_copy", fault_at)
    yedek_bilgi = create_verified_backup(target, recovery_dir, etiket=version_label)
    _fault("after_backup_copy", fault_at)
    _fault("before_backup_verification", fault_at)
    if sha256_of(yedek_bilgi["backup_path"]) != kimlik["sha256"]:
        raise ControllerRefused("yedek dogrulamasi basarisiz")
    _fault("after_backup_verification", fault_at)
    gecilen.append("verified_immutable_backup")

    # ── KAPI 12-13: fresh delta + 4B2 sertifikasyon (MOTOR) ────────────
    os.makedirs(scratch_dir, exist_ok=True)
    ref_head = build_canonical_reference(scratch_dir, CANONICAL_HEAD)
    yayim_adayi = working_path + ".r4c1-publish-candidate"
    for kalinti in (yayim_adayi,):
        if os.path.exists(kalinti):
            os.remove(kalinti)

    _fault("before_working_adoption", fault_at)
    try:
        motor = adopt_unversioned_copy(
            working_path,
            source_path=source_path,
            rollback_path=rollback_path,
            canonical_target=yayim_adayi,
            scratch_dir=scratch_dir,
            expected_source_sha256=expected_sha256,
            confirm_disposable_copy=True,
        )
    except AdoptionRefused as exc:
        raise ControllerRefused("motor adoption reddetti: " + str(exc)) from exc
    _fault("after_working_adoption", fault_at)
    gecilen.extend(["fresh_delta_gate", "engine_certification"])

    # ── ATOMIK YAYIM ───────────────────────────────────────────────────
    _fault("before_atomic_publish", fault_at)
    os.replace(yayim_adayi, target)
    _fault("after_atomic_publish", fault_at)
    gecilen.append("atomic_publish")

    # ── KAPI 14: publish SONRASI sertifikasyon ─────────────────────────
    _fault("during_post_publish_certification", fault_at)
    hatalar = certify_canonical_equivalence(
        target, ref_head, expect_terminal=True, source_manifest=motor.row_counts_before)
    satir_sonra = row_manifest(target)
    for t, n in satir_once.items():
        if satir_sonra.get(t) != n:
            hatalar.append(t + ": satir korunumu ihlali " + str(n) + " -> " + str(satir_sonra.get(t)))

    if hatalar:
        # ── KAPI 15: DOGRULANMIS atomik geri alma ──────────────────────
        _fault("before_rollback_replace", fault_at)
        durum = _atomic_restore(yedek_bilgi["backup_path"], target, kimlik["sha256"])
        _fault("after_rollback_replace", fault_at)
        _fault("during_rollback_certification", fault_at)
        # Basari IDDIASI URETILMEZ; audit YAZILMAZ.
        raise ControllerRefused(
            "publish sonrasi sertifikasyon BASARISIZ (" + "; ".join(hatalar[:5])
            + ") | recovery_state=" + durum)
    gecilen.append("post_publish_certification")

    # ── KAPI 16: audit YALNIZ terminal basaridan sonra ─────────────────
    if authorization is not None:
        _fault("before_authorization_consume", fault_at)
        _consume_nonce(ledger_dir, authorization.nonce)
        _fault("after_authorization_consume", fault_at)
        gecilen.append("authorization_consumed")

    rapor = CutoverReport(
        outcome="ADOPTED" if mod == "PRODUCTION" else "REHEARSED",
        mode=mod,
        recovery_state=RECOVERY_UNTOUCHED,
        target_realpath=kimlik["realpath"],
        target_sha256_before=kimlik["sha256"],
        target_sha256_after=sha256_of(target),
        backup_path=yedek_bilgi["backup_path"],
        backup_sha256=yedek_bilgi["backup_sha256"],
        terminal_revision=motor.terminal_revision,
        integrity_check=motor.integrity_check,
        foreign_key_violations=motor.foreign_key_violations,
        row_counts_before=satir_once,
        row_counts_after=satir_sonra,
        gates_passed=tuple(gecilen),
        accepted_data_variants=motor.accepted_data_variants,
        authorization_nonce=authorization.nonce if authorization else "",
    )
    _fault("before_audit_commit", fault_at)
    _write_audit(target, {
        "outcome": rapor.outcome,
        "mode": rapor.mode,
        "terminal_revision": rapor.terminal_revision,
        "target_sha256_before": rapor.target_sha256_before,
        "target_sha256_after": rapor.target_sha256_after,
        "backup_sha256": rapor.backup_sha256,
        "row_counts_before": rapor.row_counts_before,
        "row_counts_after": rapor.row_counts_after,
        "gates_passed": list(rapor.gates_passed),
        "accepted_data_variants": list(rapor.accepted_data_variants),
        "integrity_check": rapor.integrity_check,
        "foreign_key_violations": rapor.foreign_key_violations,
    })
    _fault("after_audit_commit", fault_at)
    return rapor


__all__ = [
    "AUDIT_SUFFIX",
    "AUDIT_VERSION",
    "CONTROLLER_FAULT_POINTS",
    "RECOVERY_MANUAL_REQUIRED",
    "RECOVERY_RESTORED",
    "RECOVERY_UNTOUCHED",
    "AuthorizationManifest",
    "ControllerRefused",
    "CutoverReport",
    "assert_physically_distinct",
    "assert_same_volume_and_atomic_replace_possible",
    "assert_sufficient_disk_space",
    "create_verified_backup",
    "issue_authorization",
    "read_audit",
    "row_manifest",
    "run_cutover",
    "running_gelka_processes",
    "sha256_of",
    "timestamp_fields",
    "validate_authorization",
    "verify_production_identity",
]
