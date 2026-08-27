'use strict';
/**
 * S5-R03D — main.js'in `validateDownloadUrl()` (offer/sözleşme PDF indirme
 * IPC güvenlik doğrulaması) testleri.
 *
 * Bağımlılıksız, Node'un kendi `assert`iyle çalışır (`node main.test.js`,
 * `electron/` dizininden). Repoda Jest YOK; dbRouting.test.js ile aynı
 * minimal-bağımlılık kültürüne uyulur.
 *
 * ÖNEMLİ: main.js dosyanın tepesinde top-level gerçek Electron API'lerine
 * bağımlı (`!app.isPackaged`, `app.requestSingleInstanceLock()`) — düz
 * `require('./main.js')` gerçek Electron çalışma-zamanı OLMADAN bu satırlarda
 * çöker. Bu yüzden `electron` modülünü, main.js'i YÜKLEMEDEN ÖNCE,
 * require.cache'e enjekte edilen zararsız bir sahte nesneyle değiştiriyoruz:
 * `app.whenReady()` hiçbir zaman resolve olmayan bir Promise döner (gerçek
 * backend/pencere ASLA başlamaz), `ipcMain.handle` yalnız kaydeder/çağırmaz.
 * main.js'in KENDİSİNE bu test dosyası için SIFIR davranış değişikliği
 * gerekmedi (yalnız dosya sonunda test-erişimi için module.exports var).
 */
const assert = require('assert');
const path = require('path');
const os = require('os');
const Module = require('module');

// ── Sahte 'electron' modülünü require.cache'e enjekte et ───────────────────
const electronCozumYolu = require.resolve('electron');
const sahteApp = {
  isPackaged: false,
  requestSingleInstanceLock: () => true,
  on: () => {},
  whenReady: () => new Promise(() => {}), // asla resolve olmaz — gercek baslatma calismaz
  quit: () => {},
  getPath: () => os.tmpdir(),
};
class SahteBrowserWindow {
  constructor() {}
  once() {}
  on() {}
  loadURL() { return Promise.resolve(); }
  loadFile() { return Promise.resolve(); }
  static getAllWindows() { return []; }
  static fromWebContents() { return null; }
}
const sahteElectron = {
  app: sahteApp,
  BrowserWindow: SahteBrowserWindow,
  dialog: { showErrorBox: () => {}, showSaveDialog: async () => ({ canceled: true }) },
  ipcMain: { handle: () => {} },
  shell: { openPath: async () => {} },
};
require.cache[electronCozumYolu] = {
  id: electronCozumYolu,
  filename: electronCozumYolu,
  loaded: true,
  exports: sahteElectron,
};

const { validateDownloadUrl, ALLOWED_PATH_PREFIXES, ALLOWED_DOWNLOAD_ORIGINS, OFFER_DOWNLOAD_PATH_MATCHER } =
  require('./main.js');

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

const OK_ORIGIN = 'http://127.0.0.1:8000';

// ── POZİTİF: offer-download exact route ─────────────────────────────────────

test('offer-download: gecerli tek basamakli ID kabul edilir', () => {
  const r = validateDownloadUrl(`${OK_ORIGIN}/offers/1/download`);
  assert.strictEqual(r.ok, true, JSON.stringify(r));
});

test('offer-download: birden fazla basamakli gecerli ID kabul edilir', () => {
  const r = validateDownloadUrl(`${OK_ORIGIN}/offers/123456/download`);
  assert.strictEqual(r.ok, true, JSON.stringify(r));
});

test('offer-download: localhost origin de kabul edilir (canonical loopback)', () => {
  const r = validateDownloadUrl('http://localhost:8000/offers/1/download');
  assert.strictEqual(r.ok, true, JSON.stringify(r));
});

test('contract-download: mevcut davranis BOZULMADI (regresyon)', () => {
  const r = validateDownloadUrl(`${OK_ORIGIN}/api/contracts/1/download`);
  assert.strictEqual(r.ok, true, JSON.stringify(r));
});

test('legacy generate-pdf: mevcut davranis BOZULMADI (regresyon)', () => {
  const r = validateDownloadUrl(`${OK_ORIGIN}/generate-pdf-simple`);
  assert.strictEqual(r.ok, true, JSON.stringify(r));
});

// ── NEGATİF: owner S5-R03D Bölüm 5 tam matris ───────────────────────────────

const negatifler = [
  ['/offers/', `${OK_ORIGIN}/offers/`],
  ['/offers/0/download', `${OK_ORIGIN}/offers/0/download`],
  ['/offers/-1/download', `${OK_ORIGIN}/offers/-1/download`],
  ['/offers/1 (indirme yolu yok)', `${OK_ORIGIN}/offers/1`],
  ['/offers/1/download/ (sondaki slash)', `${OK_ORIGIN}/offers/1/download/`],
  ['/offers/1/download/extra', `${OK_ORIGIN}/offers/1/download/extra`],
  ['/offers/abc/download (numeric degil)', `${OK_ORIGIN}/offers/abc/download`],
  ['/offers/1/generate-pdf (farkli aksiyon)', `${OK_ORIGIN}/offers/1/generate-pdf`],
  ['/offers/1/download?x=1 (query)', `${OK_ORIGIN}/offers/1/download?x=1`],
  ['/offers/1/download#fragment', `${OK_ORIGIN}/offers/1/download#fragment`],
  ['/offers//1/download (cift slash)', `${OK_ORIGIN}/offers//1/download`],
  ['/offers/../1/download (dot-segment)', `${OK_ORIGIN}/offers/../1/download`],
  ['/offers/%2e%2e/1/download (encoded dot-segment)', `${OK_ORIGIN}/offers/%2e%2e/1/download`],
  ['/offers/1%2fdownload (encoded slash)', `${OK_ORIGIN}/offers/1%2fdownload`],
  ['/offers/1%5cdownload (encoded backslash)', `${OK_ORIGIN}/offers/1%5cdownload`],
  ['/offers/%252e%252e/1/download (double-encoded)', `${OK_ORIGIN}/offers/%252e%252e/1/download`],
  ['\\offers\\1\\download (duz backslash, semasiz)', '\\offers\\1\\download'],
  ['//external-host/offers/1/download (protokol-relative)', `${OK_ORIGIN}//external-host/offers/1/download`],
  ['http://external-host/offers/1/download (yabanci origin)', 'http://external-host/offers/1/download'],
  ['canonical-host.evil.example/offers/1/download (semasiz)', 'canonical-host.evil.example/offers/1/download'],
  ['127.0.0.1 + yanlis port', 'http://127.0.0.1:9999/offers/1/download'],
  ['127.0.0.1 + userinfo', 'http://user:pass@127.0.0.1:8000/offers/1/download'],
  ['control character (\\r\\n) icerir', `${OK_ORIGIN}/offers/1/download\r\nEvil-Header: x`],
  ['whitespace icerir (orta)', `${OK_ORIGIN}/offers/1 /download`],
];

for (const [ad, rawUrl] of negatifler) {
  test(`REDDEDILMELI: ${ad}`, () => {
    const r = validateDownloadUrl(rawUrl);
    assert.strictEqual(r.ok, false, `beklenen ret, gelen: ${JSON.stringify(r)} (url=${JSON.stringify(rawUrl)})`);
  });
}

// ── Mutasyon kapıları (statik kaynak analizi) ───────────────────────────────

const mainJsMetin = require('fs').readFileSync(path.join(__dirname, 'main.js'), 'utf-8');

test('MUTASYON: OFFER_DOWNLOAD_PATH_MATCHER exact-route regex olarak tanimli (genis prefix DEGIL)', () => {
  assert.ok(
    /OFFER_DOWNLOAD_PATH_MATCHER\s*=\s*\/\^\\\/offers\\\/\[1-9\]\\d\*\\\/download\$\//.test(mainJsMetin),
    "regex kaynakta bulunamadi veya genisletilmis olabilir"
  );
  // Ayrica CANLI davranisla da dogrula: '/offers/' genis prefix'i asla kabul EDILMEMELI.
  assert.strictEqual(OFFER_DOWNLOAD_PATH_MATCHER.test('/offers/1/download/anything'), false);
  assert.strictEqual(OFFER_DOWNLOAD_PATH_MATCHER.test('/offers/1x/download'), false);
});

test('MUTASYON: query kabul EDILMIYOR (canli davranis)', () => {
  const r = validateDownloadUrl(`${OK_ORIGIN}/offers/1/download?a=1`);
  assert.strictEqual(r.ok, false);
});

test('MUTASYON: yabanci origin kabul EDILMIYOR (canli davranis)', () => {
  const r = validateDownloadUrl('http://evil.example/offers/1/download');
  assert.strictEqual(r.ok, false);
});

test('MUTASYON: validation dialog ACILMADAN ONCE cagriliyor (kaynak sirasi)', () => {
  const idxValidateCagrisi1 = mainJsMetin.indexOf("validateDownloadUrl(normalizedUrl)");
  const idxShowSaveDialog1 = mainJsMetin.indexOf('dialog.showSaveDialog');
  assert.ok(idxValidateCagrisi1 !== -1 && idxShowSaveDialog1 !== -1, 'beklenen kod parcalari bulunamadi');
  assert.ok(idxValidateCagrisi1 < idxShowSaveDialog1, 'validateDownloadUrl dialog SONRASINDA cagriliyor gibi gorunuyor');
});

test('MUTASYON: ALLOWED_PATH_PREFIXES genisletilmedi (yalniz 2 legacy giris)', () => {
  assert.deepStrictEqual(ALLOWED_PATH_PREFIXES, ['/generate-pdf', '/api/contracts']);
});

test('MUTASYON: ALLOWED_DOWNLOAD_ORIGINS yalniz loopback', () => {
  for (const o of ALLOWED_DOWNLOAD_ORIGINS) {
    assert.ok(o.startsWith('http://127.0.0.1:') || o.startsWith('http://localhost:'), o);
  }
});

// ── Sonuç ────────────────────────────────────────────────────────────────

console.log(`${gecti} test PASS, ${basarisiz.length} test FAIL`);
if (basarisiz.length > 0) {
  for (const { ad, err } of basarisiz) {
    console.error(`  FAIL: ${ad}\n    ${err.message}`);
  }
  process.exit(1);
}
