'use strict';
/**
 * PDSMR — packaging-files-closure.test.js
 *
 * Kalici duzenleyici test: electron-builder'in "files" sozlesmesi, main.js
 * ve preload.js'in GERCEKTEN require() ettigi TUM yerel (relative) modulleri
 * kapsiyor mu, diye statik olarak dogrular. Bu, RC v1.0.12'nin coktugu KOK
 * NEDENI (dbRouting.js/gateRefusalParser.js paketlemeye dahil edilmemisti,
 * ama main.js onlari require() ediyordu) bir DAHA SESSIZCE olusmasin diye
 * yazildi.
 *
 * Ilke: dbRouting.test.js ile ayni - Node'un kendi `assert`iyle calisir
 * (`node packaging-files-closure.test.js`). Jest YOK, repo kulturune uyar.
 * Dosya adlari HARDCODE EDILMEDI - main.js/preload.js icindeki GERCEK
 * require() cagrilarindan DINAMIK olarak cikarilir, boylece gelecekte
 * eklenecek YENI bir relative require de otomatik yakalanir.
 */
const assert = require('assert');
const fs = require('fs');
const path = require('path');

const ELECTRON_DIR = __dirname;
const GIRIS_DOSYALARI = ['main.js', 'preload.js'];

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

/** main.js/preload.js kaynagindan TUM relative (./ ile baslayan) require() hedeflerini cikarir. */
function relativeRequireHedefleriniCikar(dosyaYolu) {
  const kaynak = fs.readFileSync(dosyaYolu, 'utf8');
  const desen = /require\(\s*['"](\.\/[^'"]+)['"]\s*\)/g;
  const hedefler = [];
  let eslesme;
  while ((eslesme = desen.exec(kaynak)) !== null) {
    hedefler.push(eslesme[1]);
  }
  return hedefler;
}

/** './dbRouting' gibi bir require hedefini gercek dosya adina cozer (Node'un
 *  kendi cozumleme sirasina yakin: tam eslesme, sonra .js, sonra .json). */
function dosyaAdinaCoz(hedef) {
  const adaylar = [hedef, `${hedef}.js`, `${hedef}.json`];
  for (const aday of adaylar) {
    if (fs.existsSync(path.join(ELECTRON_DIR, aday))) {
      return aday.replace(/^\.\//, '');
    }
  }
  return null;
}

const pkg = JSON.parse(fs.readFileSync(path.join(ELECTRON_DIR, 'package.json'), 'utf8'));
const filesListesi = (pkg.build && Array.isArray(pkg.build.files)) ? pkg.build.files : [];

for (const girisDosyasi of GIRIS_DOSYALARI) {
  const girisYolu = path.join(ELECTRON_DIR, girisDosyasi);
  if (!fs.existsSync(girisYolu)) {
    test(`${girisDosyasi} mevcut olmali`, () => {
      throw new Error(`giris dosyasi bulunamadi: ${girisYolu}`);
    });
    continue;
  }

  const hedefler = relativeRequireHedefleriniCikar(girisYolu);
  test(`${girisDosyasi}: relative require cikarma calisti (${hedefler.length} hedef bulundu)`, () => {
    assert.ok(Array.isArray(hedefler));
  });

  for (const hedef of hedefler) {
    const cozulenDosya = dosyaAdinaCoz(hedef);

    test(`${girisDosyasi} -> require('${hedef}'): kaynak dosya diskte mevcut`, () => {
      assert.ok(
        cozulenDosya !== null,
        `'${hedef}' hicbir aday uzantiyla (${hedef}, ${hedef}.js, ${hedef}.json) diskte bulunamadi`
      );
    });

    if (cozulenDosya !== null) {
      test(`${girisDosyasi} -> require('${hedef}'): electron-builder "files" sozlesmesi '${cozulenDosya}'yi kapsiyor`, () => {
        assert.ok(
          filesListesi.includes(cozulenDosya),
          `package.json build.files = ${JSON.stringify(filesListesi)} icinde '${cozulenDosya}' YOK - ` +
          `paketlenmis app'te bu modul EKSIK OLACAK ve main process ilk require()'de cokecek ` +
          `(RC v1.0.12'nin gercek Sandbox'ta yasadigi cokme TAM OLARAK buydu)`
        );
      });
    }
  }
}

console.log(`\n${gecen} kontrol PASS.`);
if (process.exitCode === 1) {
  console.error('BAZI KONTROLLER FAIL - paketlenmis app baslangicta cokebilir.');
}
