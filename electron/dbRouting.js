'use strict';
/**
 * PDSMR-R2 — packaged backend icin DATABASE_URL yonlendirme karari.
 *
 * Saf modul: Electron'a BAGIMLI DEGILDIR (yalniz `path`/`fs`), boylece
 * gercek Electron calistirmadan test edilebilir. main.js bu modulun
 * dondugu karari SPAWN ENV'ine DATABASE_URL olarak gecirir.
 *
 * Kural (owner sozlesmesi, PDSMR-R2 ADIM 7):
 *   - canonical (`<userData>/database/gelka_enerji.db`) VARSA -> onu kullan.
 *   - canonical YOK ama legacy (`resources/backend/gelka_enerji.db`) VARSA
 *     -> bu, kurtarmanin CALISMADIGI/BASARISIZ oldugu anlamina gelir.
 *     SESSIZCE resources altinda yeni BOS bir DB ile devam ETME — FAIL
 *     CLOSED don, backend'i baslatma.
 *   - ikisi de YOKSA -> GERCEK taze kurulum; backend kendi ilk-kurulum
 *     yolundan (create_all/migration) canonical'i olusturabilir.
 *
 * Cagrildigi yerler:
 * - electron/main.js::startBackend() [PDSMR-R2]
 * - electron/dbRouting.test.js
 */

const path = require('path');
const fs = require('fs');

const DATABASE_SUBDIR = 'database';
const CANONICAL_DB_FILENAME = 'gelka_enerji.db';

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
  CANONICAL_DB_FILENAME,
  MODE_CANONICAL,
  MODE_FRESH_INSTALL,
  MODE_FAIL_CLOSED_MISSING_RESCUE,
  resolveCanonicalDbPath,
  resolveLegacyDbPath,
  resolveDatabaseRouting,
  toSqliteUrl,
};
