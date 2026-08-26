'use strict';
/**
 * PDSMR-R2 — dbRouting.js testleri.
 *
 * Bagimliliksiz, Node'un kendi `assert`iyle calisir (`node dbRouting.test.js`).
 * Repoda Jest YOK; yeni bir test-runner bagimliligi eklemek yerine, mevcut
 * repo kulturune (minimal bagimlilik) uyan bu yol secildi.
 */
const assert = require('assert');
const fs = require('fs');
const os = require('os');
const path = require('path');

const {
  resolveCanonicalDbPath,
  resolveDatabaseRouting,
  toSqliteUrl,
  MODE_CANONICAL,
  MODE_FRESH_INSTALL,
  MODE_FAIL_CLOSED_MISSING_RESCUE,
  resolveDurableStorageDir,
  resolveLegacyStorageDir,
} = require('./dbRouting');

let gecti = 0;
let basarisiz = [];

function test(ad, fn) {
  try {
    fn();
    gecti += 1;
  } catch (err) {
    basarisiz.push({ ad, err });
  }
}

function mkArena() {
  const kok = fs.mkdtempSync(path.join(os.tmpdir(), 'pdsmr-r2-'));
  const userDataDir = path.join(kok, 'AppData', 'Roaming', 'gelka-enerji');
  const resourcesPath = path.join(kok, 'app', 'resources');
  fs.mkdirSync(path.join(resourcesPath, 'backend'), { recursive: true });
  return { kok, userDataDir, resourcesPath };
}

function yazBosDb(dosyaYolu) {
  fs.mkdirSync(path.dirname(dosyaYolu), { recursive: true });
  fs.writeFileSync(dosyaYolu, Buffer.from('SQLite format 3\0'));
}

// ── userData formulu Python tarafiyla AYNI ───────────────────────────────
test('canonical path = userData/database/gelka_enerji.db', () => {
  const p = resolveCanonicalDbPath('C:\\Users\\x\\AppData\\Roaming\\gelka-enerji');
  assert.strictEqual(
    p,
    path.join('C:\\Users\\x\\AppData\\Roaming\\gelka-enerji', 'database', 'gelka_enerji.db')
  );
});

test('userData formulu kullanici adi icerigine bagli DEGIL (bosluk/TR karakter)', () => {
  const varyantlar = ['ulastelli', 'Mehmet Ali', 'Şükrü Öztürk', 'user with spaces'];
  const sonuclar = new Set(
    varyantlar.map((ad) => {
      const kok = `C:\\Users\\${ad}\\AppData\\Roaming\\gelka-enerji`;
      return resolveCanonicalDbPath(kok).replace(kok, '<KOK>');
    })
  );
  assert.strictEqual(sonuclar.size, 1, 'formul kullanici adina gore degisti');
});

// ── sqlite URL: bosluk/TR karakter RAW kalmali (URI-encode YOK) ─────────
test('toSqliteUrl bosluklu yolu URI-encode ETMEZ', () => {
  const url = toSqliteUrl('C:\\Program Files\\gelka enerji\\gelka_enerji.db');
  assert.ok(url.includes(' '), 'bosluk %20 ile encode edilmis');
  assert.strictEqual(url, 'sqlite:///C:/Program Files/gelka enerji/gelka_enerji.db');
});

test('toSqliteUrl TR karakteri bozmadan gecirir', () => {
  const url = toSqliteUrl('C:\\Users\\Şükrü\\AppData\\Roaming\\gelka-enerji\\database\\gelka_enerji.db');
  assert.ok(url.includes('Şükrü'));
});

// ── yonlendirme karari: A/B/C durumlari ──────────────────────────────────
test('canonical VAR -> CANONICAL modu, canonical URL doner', () => {
  const { userDataDir, resourcesPath } = mkArena();
  yazBosDb(resolveCanonicalDbPath(userDataDir));
  const karar = resolveDatabaseRouting({ userDataDir, resourcesPath });
  assert.strictEqual(karar.mode, MODE_CANONICAL);
  assert.ok(karar.url.includes('database'));
});

test('canonical YOK, legacy VAR -> FAIL_CLOSED (sessiz bos DB dusmesi YOK)', () => {
  const { userDataDir, resourcesPath } = mkArena();
  yazBosDb(path.join(resourcesPath, 'backend', 'gelka_enerji.db'));
  const karar = resolveDatabaseRouting({ userDataDir, resourcesPath });
  assert.strictEqual(karar.mode, MODE_FAIL_CLOSED_MISSING_RESCUE);
  assert.strictEqual(karar.url, null, 'FAIL_CLOSED durumunda URL verilmemeli');
});

test('ikisi de YOK -> FRESH_INSTALL, canonical URL doner (yeni DB icin)', () => {
  const { userDataDir, resourcesPath } = mkArena();
  const karar = resolveDatabaseRouting({ userDataDir, resourcesPath });
  assert.strictEqual(karar.mode, MODE_FRESH_INSTALL);
  assert.ok(karar.url.includes('database'));
});

test('canonical VE legacy IKISI DE VAR -> canonical ONCELIKLIDIR (legacy artik onemsiz)', () => {
  const { userDataDir, resourcesPath } = mkArena();
  yazBosDb(resolveCanonicalDbPath(userDataDir));
  yazBosDb(path.join(resourcesPath, 'backend', 'gelka_enerji.db'));
  const karar = resolveDatabaseRouting({ userDataDir, resourcesPath });
  assert.strictEqual(karar.mode, MODE_CANONICAL);
});

test('resolveDatabaseRouting YAN ETKISIZ — dizin/dosya OLUSTURMAZ', () => {
  const { userDataDir, resourcesPath } = mkArena();
  resolveDatabaseRouting({ userDataDir, resourcesPath });
  assert.ok(!fs.existsSync(path.join(userDataDir, 'database')), 'fonksiyon dizin olusturdu');
});

// ── S5-R03B: durable storage kok yonlendirmesi ──────────────────────────
test('durable storage kok = userData/storage (DB ile SIMETRIK)', () => {
  const p = resolveDurableStorageDir('C:\\Users\\x\\AppData\\Roaming\\gelka-enerji');
  assert.strictEqual(
    p,
    path.join('C:\\Users\\x\\AppData\\Roaming\\gelka-enerji', 'storage')
  );
});

test('legacy storage kok = resourcesPath/backend/storage', () => {
  const p = resolveLegacyStorageDir('C:\\inst\\resources');
  assert.strictEqual(p, path.join('C:\\inst\\resources', 'backend', 'storage'));
});

test('durable storage kok formulu kullanici adi icerigine bagli DEGIL', () => {
  const varyantlar = ['ulastelli', 'Mehmet Ali', 'Şükrü Öztürk', 'user with spaces'];
  const sonuclar = new Set(
    varyantlar.map((ad) => {
      const kok = `C:\\Users\\${ad}\\AppData\\Roaming\\gelka-enerji`;
      return resolveDurableStorageDir(kok).replace(kok, '<KOK>');
    })
  );
  assert.strictEqual(sonuclar.size, 1, 'formul kullanici adina gore degisti');
});

test('durable ve legacy storage kokleri FARKLI dizinlerdir (DB routing ile ayni ikili yapi)', () => {
  const { userDataDir, resourcesPath } = mkArena();
  const durable = resolveDurableStorageDir(userDataDir);
  const legacy = resolveLegacyStorageDir(resourcesPath);
  assert.notStrictEqual(durable, legacy);
  assert.ok(durable.startsWith(userDataDir));
  assert.ok(legacy.startsWith(resourcesPath));
});

test('resolveDurableStorageDir/resolveLegacyStorageDir YAN ETKISIZ — dizin OLUSTURMAZ', () => {
  const { userDataDir, resourcesPath } = mkArena();
  resolveDurableStorageDir(userDataDir);
  resolveLegacyStorageDir(resourcesPath);
  assert.ok(!fs.existsSync(path.join(userDataDir, 'storage')), 'fonksiyon dizin olusturdu');
  assert.ok(!fs.existsSync(path.join(resourcesPath, 'backend', 'storage')), 'fonksiyon dizin olusturdu');
});

// ── sonuc ─────────────────────────────────────────────────────────────
if (basarisiz.length) {
  console.error(`\n${basarisiz.length} test BASARISIZ:\n`);
  for (const { ad, err } of basarisiz) {
    console.error(`  FAIL: ${ad}\n    ${err.message}`);
  }
  process.exit(1);
} else {
  console.log(`${gecti} test PASS`);
  process.exit(0);
}
