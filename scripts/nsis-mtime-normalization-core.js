"use strict";
/**
 * GELKA-S5-RC1-R31-ELECTRON-BUILDER-UNINSTALLER-MTIME-NORMALIZATION-INTEGRATION-R32
 * nsis-mtime-normalization-core.js -- patch-nsis-mtime-normalization-r32.js
 * tarafindan NsisTarget.js'e enjekte edilen TEK satirlik require() cagrisinin
 * cagirdigi ASIL mantik. Ayri dosyada tutulmasinin nedeni: patch REPLACEMENT
 * metni KUCUK/BASIT kalsin (minimal candidate patch, owner madde 14), KARMASIK
 * mantik (epoch dogrulama, content-hash-snapshot, receipt uretimi) BAGIMSIZ
 * test edilebilir bir modulde yasasin (owner madde 29-33 prefreeze qualification).
 */
const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

function sha256File(p) { return crypto.createHash("sha256").update(fs.readFileSync(p)).digest("hex"); }

/** validateEpoch -- owner madde 12-13: decimal integer, guvenli aralik, env
 * yok/malformed/range-disiysa sessizce current time KULLANMADAN HARD STOP. */
function validateEpoch(rawEpoch) {
    if (rawEpoch == null || rawEpoch === "") {
        throw new Error('GELKA-NSIS-MTIME-NORMALIZATION-R32: SOURCE_DATE_EPOCH env degiskeni set degil - fail-closed.');
    }
    if (!/^[0-9]+$/.test(rawEpoch)) {
        throw new Error('GELKA-NSIS-MTIME-NORMALIZATION-R32: SOURCE_DATE_EPOCH sayisal (decimal integer) degil: "' + rawEpoch + '" - fail-closed.');
    }
    const epochSeconds = Number(rawEpoch);
    // Alt sinir 0 (1970-01-01 UTC), ust sinir 4102444800 (2100-01-01 UTC) --
    // guvenli/makul araligin disinda bir deger hesaplama hatasina isaret eder.
    if (!Number.isSafeInteger(epochSeconds) || epochSeconds < 0 || epochSeconds > 4102444800) {
        throw new Error('GELKA-NSIS-MTIME-NORMALIZATION-R32: SOURCE_DATE_EPOCH guvenli aralik disinda: "' + rawEpoch + '" - fail-closed.');
    }
    const isEven = epochSeconds % 2 === 0; // owner madde 12: "tercihen cift saniye" (FAT/NTFS 2sn cozunurluk uyumu) -- BILGI amacli, HARD STOP tetiklemez.
    return { epochSeconds, isEven };
}

/** normalizeOneFile -- TEK bir build-generated girdiyi (APP_64/APP_ARM64/
 * APP_32/UNINSTALLER_OUT_FILE) SADECE LastWriteTime'i SOURCE_DATE_EPOCH'a
 * normalize eder. CreationTime VE LastAccessTime'a DOKUNULMAZ (owner madde 17).
 * Icerik hash/size normalizasyon ONCESI/SONRASI karsilastirilir (owner madde 16). */
function normalizeOneFile(inputPath, epochSeconds, receiptEntries) {
    if (!fs.existsSync(inputPath)) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: normalize edilecek dosya bulunamadi: " + inputPath + " - fail-closed.");
    }
    // Kapi (owner madde 18): symlink/reparse -- lstat KULLANILIR (stat DEGIL,
    // cunku stat sembolik baglantiyi TAKIP eder, biz BAGLANTININ KENDISINI kontrol ediyoruz).
    const lstatResult = fs.lstatSync(inputPath);
    if (lstatResult.isSymbolicLink()) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: hedef symlink/reparse point: " + inputPath + " - fail-closed.");
    }
    if (!lstatResult.isFile()) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: hedef regular file degil (dizin/ozel dosya turu): " + inputPath + " - fail-closed.");
    }

    const statBefore = fs.statSync(inputPath);
    const contentSha256Before = sha256File(inputPath);
    const sizeBefore = statBefore.size;
    const atimeSecondsOriginal = Math.floor(statBefore.atimeMs / 1000);
    const mtimeSecondsBefore = Math.floor(statBefore.mtimeMs / 1000);
    const creationTimeMsBefore = statBefore.birthtimeMs;

    // SADECE mtime degisir -- fs.utimesSync atime/mtime'i BIRLIKTE parametre
    // ister (ayri ayarlama API'si YOK) -- atime'a KENDI ORIJINAL (saniyeye
    // yuvarlanmis) degeri GERI verilerek "degismemis" hale getirilir.
    fs.utimesSync(inputPath, atimeSecondsOriginal, epochSeconds);

    const statAfter = fs.statSync(inputPath);
    const contentSha256After = sha256File(inputPath);
    const sizeAfter = statAfter.size;
    const mtimeSecondsAfter = Math.floor(statAfter.mtimeMs / 1000);
    const atimeSecondsAfter = Math.floor(statAfter.atimeMs / 1000);
    const creationTimeMsAfter = statAfter.birthtimeMs;

    if (mtimeSecondsAfter !== epochSeconds) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: read-back LastWriteTime dogrulamasi basarisiz: " + inputPath + " (beklenen=" + epochSeconds + " gercek=" + mtimeSecondsAfter + ") - fail-closed.");
    }
    if (atimeSecondsAfter !== atimeSecondsOriginal) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: LastAccessTime BEKLENMEDEN DEGISTI (owner madde 17 ihlali): " + inputPath + " (once=" + atimeSecondsOriginal + " sonra=" + atimeSecondsAfter + ") - fail-closed.");
    }
    if (creationTimeMsAfter !== creationTimeMsBefore) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: CreationTime BEKLENMEDEN DEGISTI (owner madde 17 ihlali): " + inputPath + " (once=" + creationTimeMsBefore + " sonra=" + creationTimeMsAfter + ") - fail-closed.");
    }
    if (contentSha256After !== contentSha256Before || sizeAfter !== sizeBefore) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: ICERIK DEGISTI (beklenmiyor, owner madde 16 ihlali): " + inputPath + " (hashOnce=" + contentSha256Before + " hashSonra=" + contentSha256After + " sizeOnce=" + sizeBefore + " sizeSonra=" + sizeAfter + ") - fail-closed.");
    }

    receiptEntries.push({
        path: inputPath,
        contentSha256Before, contentSha256After, contentUnchanged: true,
        sizeBefore, sizeAfter,
        mtimeSecondsBefore, mtimeSecondsAfter, epochApplied: epochSeconds,
        atimeSecondsOriginal, atimeSecondsAfter, atimeUnchanged: true,
        creationTimeMsBefore, creationTimeMsAfter, creationTimeUnchanged: true,
    });
}

/** gelkaNormalizeNsisInputMtimesR32 -- patch'in NsisTarget.js'e enjekte ettigi
 * TEK cagri noktasi. `defines` -- buildInstaller()'in o anki defines objesi. */
function gelkaNormalizeNsisInputMtimesR32(defines) {
    const rawEpoch = process.env.SOURCE_DATE_EPOCH;
    const { epochSeconds, isEven } = validateEpoch(rawEpoch);

    const inputPaths = [];
    ["APP_64", "APP_ARM64", "APP_32"].forEach(function (archDefineKey) {
        if (typeof defines[archDefineKey] === "string" && defines[archDefineKey].length > 0) inputPaths.push(defines[archDefineKey]);
    });
    if (typeof defines.UNINSTALLER_OUT_FILE === "string" && defines.UNINSTALLER_OUT_FILE.length > 0) inputPaths.push(defines.UNINSTALLER_OUT_FILE);
    if (inputPaths.length === 0) {
        throw new Error("GELKA-NSIS-MTIME-NORMALIZATION-R32: normalize edilecek hicbir build-generated girdi (APP_64/APP_ARM64/APP_32/UNINSTALLER_OUT_FILE) bulunamadi - fail-closed.");
    }

    const receiptEntries = [];
    inputPaths.forEach(function (p) { normalizeOneFile(p, epochSeconds, receiptEntries); });

    const receipt = {
        schemaVersion: "r32-nsis-mtime-normalization-receipt-1",
        computedAtUTC: new Date().toISOString(),
        sourceDateEpoch: epochSeconds, sourceDateEpochIsEven: isEven,
        normalizedFileCount: receiptEntries.length,
        entries: receiptEntries,
    };
    // Owner madde 42: normalization receipt, INSTALLER makensis spawn'indan
    // ONCE durable yazilmali -- bu fonksiyon TAM O NOKTADA cagrildigi icin
    // (executeMakensis'ten HEMEN ONCE), receipt burada, senkron+atomik yazilir.
    const receiptPath = process.env.GELKA_NSIS_MTIME_RECEIPT_PATH;
    if (receiptPath) {
        fs.mkdirSync(path.dirname(receiptPath), { recursive: true });
        const tmp = receiptPath + ".tmp." + process.pid;
        fs.writeFileSync(tmp, JSON.stringify(receipt, null, 2), "utf8");
        fs.renameSync(tmp, receiptPath);
    }
    return receipt;
}

module.exports = { gelkaNormalizeNsisInputMtimesR32, validateEpoch, normalizeOneFile, sha256File };
