'use strict';
/**
 * PDSMR-R3 STEP 8 — backend/run_server.py::_run_startup_schema_gate()'in
 * stderr'e yazdigi sabit onekli satirlari ayristirir.
 *
 * dbRouting.js ile AYNI ilke: Electron'a BAGIMLI DEGIL (saf fonksiyon),
 * boylece gercek Electron calistirmadan test edilebilir.
 *
 * Cagrildigi yerler:
 * - electron/main.js::startBackend() [PDSMR-R3, stderr 'data' olayinda]
 * - electron/gateRefusalParser.test.js
 */

const GATE_REFUSED_RE = /^PDSMR_R3_GATE_REFUSED\[(\d+)\]: (.*)$/;
const GATE_UNEXPECTED_RE = /^PDSMR_R3_GATE_UNEXPECTED: (.*)$/;

/**
 * Bir stderr veri parcasini (birden fazla satir icerebilir) tarar ve EN
 * SON eslesen kapi reddini doner (yoksa null). Onceki cagrilardan gelen
 * bir sonucu KORUMAK/BIRLESTIRMEK cagiranin sorumlulugudur (main.js
 * `lastGateRefusal` degiskenini boyle kullanir).
 *
 * @param {string} chunk - stderr'den gelen ham metin (bir veya cok satir).
 * @returns {{exitCode: number|null, message: string}|null}
 */
function parseGateRefusal(chunk) {
  let sonuc = null;
  for (const rawSatir of chunk.split(/\r?\n/)) {
    const satir = rawSatir.trim();
    if (!satir) continue;
    const reddedildi = GATE_REFUSED_RE.exec(satir);
    if (reddedildi) {
      sonuc = { exitCode: Number(reddedildi[1]), message: reddedildi[2] };
      continue;
    }
    const beklenmedik = GATE_UNEXPECTED_RE.exec(satir);
    if (beklenmedik) {
      sonuc = { exitCode: null, message: beklenmedik[1] };
    }
  }
  return sonuc;
}

module.exports = { GATE_REFUSED_RE, GATE_UNEXPECTED_RE, parseGateRefusal };
