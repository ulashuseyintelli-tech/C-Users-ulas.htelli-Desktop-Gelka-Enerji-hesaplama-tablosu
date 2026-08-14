"""
PyInstaller entry point for Gelka Enerji backend.
Standalone .exe olarak paketlendiğinde bu dosya çalışır.
"""
import sys
import os
import uvicorn

# PyInstaller bundled modda çalışma dizinini ayarla
if getattr(sys, 'frozen', False):
    # .exe olarak çalışıyorsa
    base_dir = os.path.dirname(sys.executable)
    os.chdir(base_dir)
    # .env dosyasını yükle
    os.environ.setdefault('DATABASE_URL', f'sqlite:///{os.path.join(base_dir, "gelka_enerji.db")}')


def _database_url_to_path(database_url: str) -> str:
    """sqlite:///<path> URL'ini dosya sistemi yoluna çevirir (dbRouting.js
    toSqliteUrl()'in TERSİ — RAW, URI-encode YOK, aynı sözleşme)."""
    onek = 'sqlite:///'
    if not database_url.startswith(onek):
        raise ValueError(f"beklenmeyen DATABASE_URL şeması: {database_url!r}")
    return database_url[len(onek):].replace('/', os.sep)


def _enforce_packaged_environment_invariants() -> None:
    """
    PDSMR-R3B STEP 5 — Electron'un enjekte ettigi degerlere KORUYUCUSUZ
    guvenmek yerine, frozen run_server BAGIMSIZ olarak dogrular: hem
    GELKA_PACKAGED_RUNTIME=1 hem ENV=desktop GERCEKTEN (gercek surec ortam
    degiskeni olarak - .env dosyasindan DEGIL, cunku pydantic-settings
    ortam degiskenlerini HER ZAMAN dotenv'den ONCELIKLENDIRIR, bkz.
    app/core/config.py) set edilmis mi.

    Owner: "direct packaged-backend launch without these invariants fails
    closed" - yani biri EKSIK/YANLISSA (ör. gelka-backend.exe Electron
    OLMADAN dogrudan cift-tiklanarak/elle bir terminalden baslatilirsa),
    backend SESSIZCE .env dosyasindaki degere (ENV=desktop) DUSMEK yerine
    ACIKCA reddeder - machine-local.env de bu ikisini EZEMEZ (electron/
    main.js::startBackend() paketlenmis dalinda ...machineLocalEnv'DEN
    SONRA literal olarak enjekte edilirler).

    Schema kapisindan (startup_gate.py) ONCE calisir - DB'ye HICBIR sekilde
    dokunulmadan, EN ERKEN noktada HARD_STOP (port hic baglanmaz).

    Cagrildigi yerler:
    - main() [PDSMR-R3B, frozen modda, _run_startup_schema_gate()'DEN de ONCE]
    """
    from app.legacy_adoption.rescue import sanitize_for_log

    beklenen_env = "desktop"
    gercek_env = os.environ.get("ENV")
    gercek_packaged = os.environ.get("GELKA_PACKAGED_RUNTIME")

    sorunlar = []
    if gercek_packaged != "1":
        sorunlar.append(f"GELKA_PACKAGED_RUNTIME={gercek_packaged!r} (beklenen: '1')")
    if gercek_env != beklenen_env:
        sorunlar.append(f"ENV={gercek_env!r} (beklenen: {beklenen_env!r})")

    if sorunlar:
        mesaj = (
            "paketlenmis calisma-zamani degismezleri dogrulanamadi: "
            + "; ".join(sorunlar)
            + " - gelka-backend.exe Electron disinda mi baslatildi? "
            "Dogrudan calistirilmamalidir."
        )
        sys.stderr.write(f"PDSMR_R3_GATE_REFUSED[60]: {sanitize_for_log(mesaj)}\n")
        sys.exit(60)


def _run_startup_schema_gate() -> None:
    """
    PDSMR-R3 STEP 3 — app.main import EDİLMEDEN, hiçbir ORM/router yan
    etkisi, init_db(), create_all(), seed rutini veya port bind İŞLEMEDEN
    ÖNCE çalışır. YALNIZ frozen (paketlenmiş) modda — dev ortamı
    backend/.env'ini kullanmaya, mevcut init_db()/create_all() davranışını
    (geriye dönük uyumlu) korumaya devam eder (owner kararı, STEP 7).

    HARD_STOP -> stderr'e sabit önekli, sanitize edilmiş bir satır yazar ve
    KATEGORİZE bir exit code ile süreci SONLANDIRIR — backend PORTU HİÇ
    BAĞLANMAZ (electron/main.js bunu Step 8'in fail-closed UX'i için
    ayrıştırır, bkz. PDSMR_R3_GATE_REFUSED öneki).

    Cagrildigi yerler:
    - main() [PDSMR-R3, app.main import'undan HEMEN ÖNCE]
    """
    from app.legacy_adoption.rescue import sanitize_for_log
    from app.legacy_adoption.startup_gate import GateRefused, run_startup_gate

    canonical_path = _database_url_to_path(os.environ['DATABASE_URL'])
    # Legacy hint: exe'nin KENDİ dizini (resources/backend/) - normal
    # akışta PDSMR-R2 rescue.exe bunu ZATEN taşımış/temizlemiş olmalı;
    # burada YALNIZ savunma amaçlı ikinci kapı (G durumu, main.js'nin
    # dbRouting.js kontrolü zaten Electron seviyesinde AYNI şeyi yapar —
    # run_server.py'nin Electron'suz/doğrudan çalıştırılma ihtimaline
    # karşı burada da tekrarlanır).
    legacy_hint = os.path.join(os.path.dirname(sys.executable), 'gelka_enerji.db')

    try:
        rapor = run_startup_gate(canonical_path, legacy_hint_path=legacy_hint)
    except GateRefused as exc:
        sys.stderr.write(
            f"PDSMR_R3_GATE_REFUSED[{exc.exit_code}]: {sanitize_for_log(str(exc))}\n"
        )
        sys.exit(exc.exit_code)
    except Exception as exc:  # beklenmeyen hata dahi create_all'a DÜŞMEZ
        sys.stderr.write(
            f"PDSMR_R3_GATE_UNEXPECTED: {type(exc).__name__}: "
            f"{sanitize_for_log(str(exc))}\n"
        )
        sys.exit(99)

    sys.stderr.write(
        f"PDSMR_R3_GATE_OK: state={rapor.state.value} action={rapor.action} "
        f"revision={rapor.terminal_revision}\n"
    )


def main():
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument('--host', default='127.0.0.1')
    parser.add_argument('--port', type=int, default=8000)
    args = parser.parse_args()

    if getattr(sys, 'frozen', False):
        _enforce_packaged_environment_invariants()
        _run_startup_schema_gate()

    # PyInstaller frozen modda string import çalışmayabilir,
    # doğrudan modülü import edip geçiriyoruz.
    from app.main import app as application
    uvicorn.run(
        application,
        host=args.host,
        port=args.port,
        log_level='info',
        workers=1,
    )

if __name__ == '__main__':
    main()
