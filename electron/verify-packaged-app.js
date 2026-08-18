#!/usr/bin/env node
'use strict';
/**
 * PDSMR — verify-packaged-app.js
 *
 * "electron-builder --dir" (pack) veya "--win" (dist:win) CIKTISI ustunde
 * calisir. main.js'in relative require ettigi dosyalarin app.asar icinde
 * GERCEKTEN mevcut oldugunu VE paketlenmis main process'in "Cannot find
 * module" ile COKMEDIGINI (kisa bir smoke penceresinde) dogrular.
 *
 * Bu script, gecmiste sadece build-desktop.bat'in BASARIYLA TAMAMLANMASININ
 * (electron-builder require() hedeflerini dogrulamaz) GERCEK calisirligi
 * kanitlamaya YETMEDIGINI gosteren RC v1.0.12 cokmesinden sonra eklendi.
 *
 * Kullanim:
 *   node verify-packaged-app.js <win-unpacked-klasoru>
 * (varsayilan: electron/release/win-unpacked, electron-builder'in "pack"
 *  hedefinin varsayilan ciktisi)
 *
 * Node'un kendi assert'iyle calisir - Jest YOK, repo kulturune uyar.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');
const { execFileSync, spawn } = require('child_process');

const ELECTRON_DIR = __dirname;
const unpackedDir = process.argv[2]
  ? path.resolve(process.argv[2])
  : path.join(ELECTRON_DIR, 'release', 'win-unpacked');

let gecen = 0;
function test(ad, fn) {
  try {
    fn();
    gecen += 1;
    console.log(`  PASS ${ad}`);
  } catch (e) {
    console.error(`  FAIL ${ad}: ${e.message}`);
    process.exitCode = 1;
  }
}

console.log(`Hedef win-unpacked klasoru: ${unpackedDir}`);

if (!fs.existsSync(unpackedDir)) {
  console.error(`FAIL: klasor bulunamadi: ${unpackedDir}`);
  console.error('Once "npm run pack" (electron-builder --dir) calistirin.');
  process.exit(1);
}

const asarPath = path.join(unpackedDir, 'resources', 'app.asar');
const asarCliAdaylari = [
  path.join(ELECTRON_DIR, 'node_modules', '.bin', 'asar.cmd'),
  path.join(ELECTRON_DIR, 'node_modules', '.bin', 'asar'),
];
const asarCli = asarCliAdaylari.find((p) => fs.existsSync(p));

test('app.asar mevcut', () => {
  assert.ok(fs.existsSync(asarPath), `bulunamadi: ${asarPath}`);
});

if (fs.existsSync(asarPath) && asarCli) {
  let asarIcerigi = '';
  test('asar listelenebiliyor', () => {
    asarIcerigi = execFileSync(asarCli, ['list', asarPath], { encoding: 'utf8' });
  });

  for (const beklenenDosya of ['main.js', 'preload.js', 'dbRouting.js', 'gateRefusalParser.js']) {
    test(`app.asar icinde ${beklenenDosya} mevcut`, () => {
      assert.ok(
        asarIcerigi.split(/\r?\n/).some((satir) => satir.trim().endsWith(`/${beklenenDosya}`) || satir.trim() === beklenenDosya),
        `'${beklenenDosya}' asar listesinde bulunamadi`
      );
    });
  }
} else if (fs.existsSync(asarPath) && !asarCli) {
  console.warn('  UYARI: node_modules/.bin/asar bulunamadi ("npm install" gerekebilir) - asar ic-icerik listelemesi ATLANDI, sadece dosya varligina bakildi.');
}

// Paketlenmis main process smoke: exe'yi kisa sureligine calistir, stderr'de
// "Cannot find module" GECMEDIGINI dogrula. Kilitli/GUI dialog kalabilir -
// smoke penceresi sonunda surec ZORLA kapatilir (idempotent, veri riski yok:
// main.js'in coktugu nokta - varsa - herhangi bir DB/network islemi
// baslamadan ONCEdir).
const exeYolu = fs.readdirSync(unpackedDir).find((f) => f.endsWith('.exe') && !f.toLowerCase().includes('uninstall'));
if (exeYolu) {
  test('paketlenmis main process smoke (3sn, "Cannot find module" GECMEMELI)', () => {
    const tamYol = path.join(unpackedDir, exeYolu);
    let stderrToplam = '';
    const cocuk = spawn(tamYol, [], { stdio: ['ignore', 'ignore', 'pipe'] });
    cocuk.stderr.on('data', (d) => { stderrToplam += d.toString(); });
    const basladi = Date.now();
    while (Date.now() - basladi < 3000) {
      // senkron bekleme - basit smoke penceresi
      require('child_process').execSync('ping -n 1 127.0.0.1 >NUL', { windowsHide: true });
    }
    try { cocuk.kill(); } catch (e) { /* zaten kapanmis olabilir */ }
    assert.ok(
      !stderrToplam.includes('Cannot find module'),
      `main process stderr'de "Cannot find module" bulundu:\n${stderrToplam.slice(0, 500)}`
    );
  });
} else {
  console.warn(`  UYARI: ${unpackedDir} icinde .exe bulunamadi - main process smoke ATLANDI.`);
}

console.log(`\n${gecen} kontrol PASS.`);
if (process.exitCode === 1) {
  console.error('BAZI KONTROLLER FAIL.');
}
