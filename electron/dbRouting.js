'use strict';
/**
 * PDSMR-R2 — packaged backend icin DATABASE_URL yonlendirme karari.
 * S5-R03B — AYNI dosyaya durable STORAGE_DIR yonlendirmesi eklendi
 * (DB ile SIMETRIK format: `<userData>/storage`; ayri "mode" karari
 * GEREKMEZ — legacy storage salt-okunur uyumluluk icin OKUNUR, DB'deki
 * FAIL_CLOSED_MISSING_RESCUE gibi baslatmayi ENGELLEYEN bir kapi yok,
 * cunku yeni yazimlar zaten dogrudan durable koke gider; backend-tarafi
 * dual-root containment app/services/storage_local.py::LocalStorage'da).
 *
 * Saf modul: Electron'a BAGIMLI DEGILDIR (yalniz `path`/`fs`), boylece
 * gercek Electron calistirmadan test edilebilir. main.js bu modulun
 * dondugu karari SPAWN ENV'ine DATABASE_URL/STORAGE_DIR olarak gecirir.
 *
 * DB kurali (owner sozlesmesi, PDSMR-R2 ADIM 7):
 *   - canonical (`<userData>/database/gelka_enerji.db`) VARSA -> onu kullan.
 *   - canonical YOK ama legacy (`resources/backend/gelka_enerji.db`) VARSA
 *     -> bu, kurtarmanin CALISMADIGI/BASARISIZ oldugu anlamina gelir.
 *     SESSIZCE resources altinda yeni BOS bir DB ile devam ETME — FAIL
 *     CLOSED don, backend'i baslatma.
 *   - ikisi de YOKSA -> GERCEK taze kurulum; backend kendi ilk-kurulum
 *     yolundan (create_all/migration) canonical'i olusturabilir.
 *
 * Storage kurali (S5-R03B): durable kok HER ZAMAN `<userData>/storage`
 * olarak gecirilir (DB'nin aksine mode secimi YOK) — backend, icindeki
 * dosyalari LocalStorage.legacy_base_dir araciligiyla eski
 * `resources/backend/storage` konumundan da OKUYABILIR (silmez,
 * tasimaz); gercek installer-fazi migrasyonu bu fazda YETKISIZ.
 *
 * Cagrildigi yerler:
 * - electron/main.js::startBackend() [PDSMR-R2 + S5-R03B]
 * - electron/dbRouting.test.js
 */

const path = require('path');
const fs = require('fs');

const DATABASE_SUBDIR = 'database';
const CANONICAL_DB_FILENAME = 'gelka_enerji.db';
// S5-R03B: DB ile SIMETRIK durable storage alt-dizini.
const STORAGE_SUBDIR = 'storage';

const MODE_CANONICAL = 'CANONICAL';
const MODE_FRESH_INSTALL = 'FRESH_INSTALL';
const MODE_FAIL_CLOSED_MISSING_RESCUE = 'FAIL_CLOSED_MISSING_RESCUE';

/**
 * userData altindaki canonical DB dizinini/dosyasini hesaplar.
 *
 * Python tarafi (`app/legacy_adoption/pathsafety.py::resolve_canonical_db_path`)
 * ile AYNI formul: `<userData>/database/gelka_enerji.db`. Iki dilde ayri
 * ama BIREBIR ayni sabit degerler kullanilir; testte esitlik kanitlanir.
 */
function resolveCanonicalDbPath(userDataDir) {
  return path.join(userDataDir, DATABASE_SUBDIR, CANONICAL_DB_FILENAME);
}

function resolveLegacyDbPath(resourcesPath) {
  return path.join(resourcesPath, 'backend', CANONICAL_DB_FILENAME);
}

/**
 * userData altindaki durable storage KOKUNU hesaplar (S5-R03B).
 *
 * Python tarafi (`app/services/storage_local.py::LocalStorage`) `base_dir`
 * olarak AYNI formulu kullanir — backend'e STORAGE_DIR env'i olarak gecirilir
 * (main.js). DB'nin aksine bu fonksiyon bir "mode" KARARI URETMEZ: durable
 * kok her zaman budur; eski konumdaki dosyalar backend tarafinda ayrica
 * `legacy_base_dir` olarak salt-okunur taninir (asagida
 * `resolveLegacyStorageDir`).
 */
function resolveDurableStorageDir(userDataDir) {
  return path.join(userDataDir, STORAGE_SUBDIR);
}

/**
 * Eski (pre-durable-root) storage kokunu hesaplar — `resources/backend/storage`.
 *
 * Bu, backend'in STORAGE_DIR env'i HIC verilmeden calisirken kendi
 * `settings.storage_dir` varsayilanini (`./storage`, CWD-goreli) neye
 * cozdugunun AYNISIDIR (CWD packaged modda daima `resources/backend`'dir —
 * bkz. main.js spawn `cwd:` + backend `run_server.py`'nin frozen `os.chdir`i).
 * Salt-okunur gecis-uyumlulugu icin kullanilir; backend BURAYA asla YAZMAZ.
 */
function resolveLegacyStorageDir(resourcesPath) {
  return path.join(resourcesPath, 'backend', STORAGE_SUBDIR);
}

/**
 * SQLAlchemy `sqlite:///` URL'ine cevirir.
 *
 * Bosluk ve TR karakter (boru, unicode) icin: yol RAW (URI-encode
 * EDILMEDEN) forward-slash'e cevrilir. SQLAlchemy'nin sqlite dialect'i
 * `sqlite:///<path>` bicimini dogrudan dosya sistemi yolu olarak okur;
 * `%20` gibi encode edilmis bir bosluk YANLIS yorumlanir (literal "%20"
 * klasor adi aranir). Bu, once ONE Alembic-tarafi `?mode=ro` URI'sinden
 * (query-string parametresi tasiyan, encode GEREKEN) BILEREK farklidir.
 */
function toSqliteUrl(absoluteDbPath) {
  const posix = absoluteDbPath.split(path.sep).join('/');
  return 'sqlite:///' + posix;
}

/**
 * DATABASE_URL yonlendirme kararini hesaplar. YAN ETKISIZDIR (yalniz
 * `fs.existsSync` ile okur; dizin OLUSTURMAZ, dosya YAZMAZ).
 *
 * @param {{userDataDir: string, resourcesPath: string}} girdi
 * @returns {{mode: string, canonicalPath: string, legacyPath: string,
 *            url: string|null, reason: string}}
 */
function resolveDatabaseRouting({ userDataDir, resourcesPath }) {
  const canonicalPath = resolveCanonicalDbPath(userDataDir);
  const legacyPath = resolveLegacyDbPath(resourcesPath);

  const canonicalExists = fs.existsSync(canonicalPath);
  const legacyExists = fs.existsSync(legacyPath);

  if (canonicalExists) {
    return {
      mode: MODE_CANONICAL,
      canonicalPath,
      legacyPath,
      url: toSqliteUrl(canonicalPath),
      reason: 'canonical DB userData altinda mevcut',
    };
  }

  if (legacyExists) {
    // Rescue (installer customInit hook'u) BASARISIZ olmus ya da hic
    // calismamis olabilir. Sessizce resources/backend altinda YENI BOS
    // bir DB'ye dusmek veri kaybi/gorunmez-bozukluk riski tasir -> FAIL CLOSED.
    return {
      mode: MODE_FAIL_CLOSED_MISSING_RESCUE,
      canonicalPath,
      legacyPath,
      url: null,
      reason: 'legacy DB bulundu ama canonical yok — pre-upgrade kurtarma calismamis olabilir',
    };
  }

  return {
    mode: MODE_FRESH_INSTALL,
    canonicalPath,
    legacyPath,
    url: toSqliteUrl(canonicalPath),
    reason: 'ne legacy ne canonical DB var — gercek taze kurulum',
  };
}

module.exports = {
  DATABASE_SUBDIR,
  STORAGE_SUBDIR,
  CANONICAL_DB_FILENAME,
  MODE_CANONICAL,
  MODE_FRESH_INSTALL,
  MODE_FAIL_CLOSED_MISSING_RESCUE,
  resolveCanonicalDbPath,
  resolveLegacyDbPath,
  resolveDurableStorageDir,
  resolveLegacyStorageDir,
  resolveDatabaseRouting,
  toSqliteUrl,
};
