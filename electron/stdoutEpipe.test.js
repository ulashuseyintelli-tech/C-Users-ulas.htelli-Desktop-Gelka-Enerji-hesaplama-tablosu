'use strict';
/**
 * stdout/stderr KIRILMA KORUMASI testleri.
 *
 * Gerçek Sandbox provasında gözlenen kusur: uygulama, stdout'u PIPE olan bir
 * ebeveynden başlatılıp ebeveyn çıkınca, sonraki `console.log` "write EPIPE"
 * ile main process'i düşürüyordu (logBackend → main.js).
 *
 * Bu testler DAR kapsamı doğrular:
 *   1) Akış kapanma hatası (EPIPE) uygulamayı DÜŞÜRMEZ ve akış kırık işaretlenir.
 *   2) Kapanma DIŞINDAKİ hatalar SESSİZCE YUTULMAZ (yeniden fırlatılır).
 *   3) Akış kırıkken logBackend konsola YAZMAZ (döngü/tekrar hata üretmez).
 *
 * Bağımsız çalışır: `node stdoutEpipe.test.js` (electron/ dizininden).
 */
const assert = require('assert');
const os = require('os');

// ── Sahte 'electron' (main.test.js ile aynı desen) ─────────────────────────
const electronCozumYolu = require.resolve('electron');
require.cache[electronCozumYolu] = {
  id: electronCozumYolu,
  filename: electronCozumYolu,
  loaded: true,
  exports: {
    app: {
      isPackaged: false,
      requestSingleInstanceLock: () => true,
      on: () => {},
      whenReady: () => new Promise(() => {}),
      quit: () => {},
      getPath: () => os.tmpdir(),
    },
    BrowserWindow: class { constructor() {} once() {} on() {} loadURL() { return Promise.resolve(); }
      loadFile() { return Promise.resolve(); } static getAllWindows() { return []; }
      static fromWebContents() { return null; } },
    dialog: { showErrorBox: () => {}, showSaveDialog: async () => ({ canceled: true }) },
    ipcMain: { handle: () => {} },
    shell: { openPath: async () => {} },
  },
};

const { akisHatasiniIsle, akisKirik, AKIS_KAPANMA_KODLARI, logBackend } = require('./main.js');

let gecti = 0;
function test(ad, fn) {
  try { fn(); console.log(`  ok  ${ad}`); gecti++; }
  catch (e) { console.error(`  HATA ${ad}: ${e.message}`); process.exitCode = 1; }
}

console.log('stdout/stderr kirilma korumasi:');

test('1) EPIPE uygulamayi DUSURMEZ ve akis kirik isaretlenir', () => {
  akisKirik.stdout = false;
  const hata = Object.assign(new Error('write EPIPE'), { code: 'EPIPE' });
  assert.doesNotThrow(() => akisHatasiniIsle('stdout', hata));
  assert.strictEqual(akisKirik.stdout, true, 'akis kirik isaretlenmeliydi');
});

test('2) EBADF ve ERR_STREAM_DESTROYED de kapanma sayilir', () => {
  for (const kod of ['EBADF', 'ERR_STREAM_DESTROYED']) {
    akisKirik.stderr = false;
    assert.doesNotThrow(() => akisHatasiniIsle('stderr', Object.assign(new Error(kod), { code: kod })));
    assert.strictEqual(akisKirik.stderr, true, `${kod} icin kirik isaretlenmeliydi`);
  }
  assert.ok(AKIS_KAPANMA_KODLARI.has('EPIPE'));
  assert.strictEqual(AKIS_KAPANMA_KODLARI.size, 3, 'kapsam DAR olmali: yalniz 3 kod');
});

test('3) Kapanma DISINDAKI hata SESSIZCE YUTULMAZ', () => {
  akisKirik.stdout = false;
  const baska = Object.assign(new Error('disk dolu'), { code: 'ENOSPC' });
  assert.throws(() => akisHatasiniIsle('stdout', baska), /disk dolu/);
  assert.strictEqual(akisKirik.stdout, false, 'ilgisiz hata akisi kirik isaretlememeli');
});

test('4) Akis kirikken logBackend konsola YAZMAZ (dongu yok)', () => {
  akisKirik.stdout = true;
  let yazim = 0;
  const eski = console.log;
  console.log = () => { yazim++; };
  try { logBackend('deneme mesaji'); } finally { console.log = eski; }
  assert.strictEqual(yazim, 0, 'kirik akisa yazilmamaliydi');
  akisKirik.stdout = false;
});

test('5) Akis saglamken logBackend konsola YAZAR', () => {
  akisKirik.stdout = false;
  let yakalanan = null;
  const eski = console.log;
  console.log = (m) => { yakalanan = m; };
  try { logBackend('merhaba'); } finally { console.log = eski; }
  assert.ok(yakalanan && String(yakalanan).includes('merhaba'), 'saglam akisa yazmaliydi');
});

console.log(`\n${gecti}/5 gecti`);
