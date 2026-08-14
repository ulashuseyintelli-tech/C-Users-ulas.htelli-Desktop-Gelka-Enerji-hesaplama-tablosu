'use strict';
/**
 * PDSMR-R3 STEP 8 — gateRefusalParser.js testleri.
 *
 * dbRouting.test.js ile AYNI ilke: Node'un kendi `assert`iyle calisir
 * (`node gateRefusalParser.test.js`). Jest YOK, repo kulturune uyar.
 */
const assert = require('assert');

const { parseGateRefusal } = require('./gateRefusalParser');

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

test('GATE_REFUSED satirini dogru ayristirir', () => {
  const sonuc = parseGateRefusal(
    'PDSMR_R3_GATE_REFUSED[40]: 013 revizyonunda ama S5 tablolari mevcut\n'
  );
  assert.deepStrictEqual(sonuc, {
    exitCode: 40,
    message: '013 revizyonunda ama S5 tablolari mevcut',
  });
});

test('GATE_UNEXPECTED satirini exitCode=null ile ayristirir', () => {
  const sonuc = parseGateRefusal('PDSMR_R3_GATE_UNEXPECTED: ValueError: bir seyler\n');
  assert.deepStrictEqual(sonuc, {
    exitCode: null,
    message: 'ValueError: bir seyler',
  });
});

test('eslesmeyen metin icin null doner', () => {
  assert.strictEqual(parseGateRefusal('INFO:     Started server process [1234]\n'), null);
  assert.strictEqual(parseGateRefusal('PDSMR_R3_GATE_OK: state=DB_ABSENT action=FRESH_INITIALIZED\n'), null);
});

test('cok satirli veri parcasinda diger [stdout] gurultuye ragmen eslesmeyi bulur', () => {
  const parca =
    'INFO:     Waiting for application startup.\n' +
    'PDSMR_R3_GATE_REFUSED[43]: legacy DB var ama canonical yok\n' +
    '';
  const sonuc = parseGateRefusal(parca);
  assert.strictEqual(sonuc.exitCode, 43);
});

test('birden fazla eslesme varsa EN SON olani doner', () => {
  const parca =
    'PDSMR_R3_GATE_REFUSED[10]: ilk\n' +
    'PDSMR_R3_GATE_REFUSED[20]: ikinci\n';
  const sonuc = parseGateRefusal(parca);
  assert.strictEqual(sonuc.exitCode, 20);
  assert.strictEqual(sonuc.message, 'ikinci');
});

test('gercek kullanici adi/tam yol iceren bir mesaj OLDUGU GIBI (degistirilmeden) tasinir - '
  + 'sanitize etme sorumlulugu run_server.py::sanitize_for_log()e aittir, parser BURADA degistirmez', () => {
  const sonuc = parseGateRefusal(
    'PDSMR_R3_GATE_REFUSED[51]: adoption reddedildi: <user> zaten sanitize edilmis\n'
  );
  assert.ok(!sonuc.message.includes('C:\\Users\\'));
});

console.log(`\n${gecen} test PASS`);
