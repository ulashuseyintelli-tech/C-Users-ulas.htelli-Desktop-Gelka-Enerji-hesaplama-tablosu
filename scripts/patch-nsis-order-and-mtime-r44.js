#!/usr/bin/env node
"use strict";
/**
 * GELKA-NSIS-DETERMINISM-R44 -- MINIMAL PRODUCTION DETERMINISM CANDIDATE.
 *
 * Cagrildigi yer:
 *   - build-desktop.bat -> Adim [5/5] (electron-builder cagrisindan HEMEN
 *     ONCE, "npm install" SONRASI). Baska hicbir yerden cagrilmaz.
 *
 * Bu betik NE YAPAR:
 *   R43'un KONTROLLU 2x2 deneyinde nedensel oldugu KANITLANAN TEK faktoru
 *   (async assembly-block emission order) + R32/R33'te ZATEN kullanilan,
 *   byte-exact dogrulanmis LastWriteTime normalizasyon mekanizmasini, AYNI
 *   PRISTINE NsisTarget.js dosyasina, TEK atomik yazma isleminde, SIRAYLA
 *   uygular:
 *
 *   A) DETERMINISTIC_ASSEMBLY_ORDER (R08 Patch A, DEGISTIRILMEDEN):
 *      computeCommonInstallerScriptHeader() icindeki paralel AsyncTaskManager
 *      KALDIRILIR, YERINE kaynak-kodun TANIMLI SIRASIYLA (pluginDir ->
 *      userPluginDir -> messages.yml -> assistedMessages.yml -> customInclude)
 *      SIRALI await KONUR. R43 kaniti: bu sira, uninstaller.exe icerigini
 *      NEDENSEL olarak SABITLER (ORDER_CAUSAL_ONLY, 8/8 hucre deterministic).
 *
 *   B) LAST_WRITE_TIME_NORMALIZATION (R32/R33'te KULLANILAN, DEGISTIRILMEDEN):
 *      buildInstaller()'in NIHAI executeMakensis() cagrisindan HEMEN ONCE,
 *      APP_64/APP_ARM64/APP_32 + UNINSTALLER_OUT_FILE'in SADECE LastWriteTime'i
 *      SOURCE_DATE_EPOCH'a normalize edilir. CreationTime VE LastAccessTime'a
 *      DOKUNULMAZ (R08'in ORIJINAL Patch C'sinden BILINCLI FARK -- o
 *      CreationTime'i DA degistiriyordu, owner tarafindan REDDEDILDI).
 *
 *   BILINCLI OLARAK DAHIL EDILMEYEN (owner R44 madde 9-12):
 *   - Stable/mkdtemp staging-parent-path remediation (R08 Patch B) --
 *     R43 KANITLADI Kİ bu faktorun HICBIR nedensel etkisi YOK
 *     (pEffectAtO0=pEffectAtO1=false). Eklemek GEREKSIZ kapsam genislemesi
 *     olurdu.
 *   - CreationTime/LastAccessTime degisikligi.
 *   - Signing/compression/payload/output-name/topology/build-sirasi degisikligi.
 *   - Lifecycle-capture kodu (bu SADECE gozlem/validation worktree'sinde,
 *     AYRI bir "observation" commit'te yasar -- candidate'a DAHIL DEGIL).
 *
 *   Iki hedef de AYNI PRISTINE dosyaya (disjoint bolgeler -- A:
 *   computeCommonInstallerScriptHeader(), B: buildInstaller() nihai
 *   executeMakensis cagrisi) karsi, TEK atomik yazma ile uygulanir: (1)
 *   pristine hash dogrulanir, (2) A anchor'i TAM 1 kez bulunur + uygulanir
 *   (bellekte), (3) sonuc uzerinde B anchor'i TAM 1 kez bulunur + uygulanir
 *   (bellekte), (4) NIHAI icerik TEK fs.writeFileSync ile yazilir. Herhangi
 *   bir asamada sapma (versiyon/hash/anchor-sayisi) fail-closed durur,
 *   HICBIR BYTE degistirilmez.
 *
 * Cikis kodu: 0 = basarili (patch uygulandi VEYA zaten patch'liydi)
 *             1 = HATA, hicbir dosya degistirilmedi (fail-closed)
 */

const fs = require("fs");
const path = require("path");
const crypto = require("crypto");

const REPO_ROOT = path.join(__dirname, "..");
const ELECTRON_DIR = path.join(REPO_ROOT, "electron");
const NODE_MODULES = process.env.GELKA_PATCH_TEST_NODE_MODULES_DIR || path.join(ELECTRON_DIR, "node_modules");

const EXPECTED_APP_BUILDER_LIB_VERSION = "25.1.8";

const NSIS_TARGET_FILE = path.join(NODE_MODULES, "app-builder-lib", "out", "targets", "nsis", "NsisTarget.js");
const PRISTINE_SHA256 = "6718c37bf31ed80a1ff7ecf5bda243bc3c7e4df7fb667ab3a2327c7617799146";

// ---- A) DETERMINISTIC_ASSEMBLY_ORDER -- R08 Patch A'dan BYTE-EXACT (owner madde 14: genisletilmeden). ----
const ORDER_ANCHOR = `        const taskManager = new builder_util_1.AsyncTaskManager(packager.info.cancellationToken);
        const pluginArch = this.isUnicodeEnabled ? "x86-unicode" : "x86-ansi";
        taskManager.add(async () => {
            scriptGenerator.addPluginDir(pluginArch, path.join(await nsisResourcePathPromise(), "plugins", pluginArch));
        });
        taskManager.add(async () => {
            const userPluginDir = path.join(packager.info.buildResourcesDir, pluginArch);
            const stat = await (0, builder_util_2.statOrNull)(userPluginDir);
            if (stat != null && stat.isDirectory()) {
                scriptGenerator.addPluginDir(pluginArch, userPluginDir);
            }
        });
        taskManager.addTask((0, nsisLang_1.addCustomMessageFileInclude)("messages.yml", packager, scriptGenerator, langConfigurator));
        if (!this.isPortable) {
            if (options.oneClick === false) {
                taskManager.addTask((0, nsisLang_1.addCustomMessageFileInclude)("assistedMessages.yml", packager, scriptGenerator, langConfigurator));
            }
            taskManager.add(async () => {
                const customInclude = await packager.getResource(this.options.include, "installer.nsh");
                if (customInclude != null) {
                    scriptGenerator.addIncludeDir(packager.info.buildResourcesDir);
                    scriptGenerator.include(customInclude);
                }
            });
        }
        await taskManager.awaitTasks();`;

const ORDER_PATCHED_MARKER = "GELKA-NSIS-DETERMINISM-R44 (ORDER, ex-R08-PatchA)";

const ORDER_REPLACEMENT = `        // ${ORDER_PATCHED_MARKER}: paralel AsyncTaskManager KALDIRILDI.
        // Once: 5 alt-gorev PARALEL calisiyordu; scriptGenerator.lines'a push
        // sirasi OS I/O tamamlanma zamanina bagliydi (nondeterministik NSIS
        // script metni -> byte-farkli outer Setup.exe). R43 KONTROLLU 2x2
        // deneyi (8/8 hucre deterministic, ORDER_CAUSAL_ONLY) BU siralamanin
        // NEDENSEL oldugunu KANITLADI. Simdi: SIRALI await -- push sirasi
        // SADECE sabit kaynak-kod sirasina bagli (kaynagin TANIMLI sirasi,
        // O0/O1 gozleminden turetilmemis).
        const pluginArch = this.isUnicodeEnabled ? "x86-unicode" : "x86-ansi";
        scriptGenerator.addPluginDir(pluginArch, path.join(await nsisResourcePathPromise(), "plugins", pluginArch));
        const userPluginDir = path.join(packager.info.buildResourcesDir, pluginArch);
        const userPluginDirStat = await (0, builder_util_2.statOrNull)(userPluginDir);
        if (userPluginDirStat != null && userPluginDirStat.isDirectory()) {
            scriptGenerator.addPluginDir(pluginArch, userPluginDir);
        }
        await (0, nsisLang_1.addCustomMessageFileInclude)("messages.yml", packager, scriptGenerator, langConfigurator);
        if (!this.isPortable) {
            if (options.oneClick === false) {
                await (0, nsisLang_1.addCustomMessageFileInclude)("assistedMessages.yml", packager, scriptGenerator, langConfigurator);
            }
            const customInclude = await packager.getResource(this.options.include, "installer.nsh");
            if (customInclude != null) {
                scriptGenerator.addIncludeDir(packager.info.buildResourcesDir);
                scriptGenerator.include(customInclude);
            }
        }`;

// ---- B) LAST_WRITE_TIME_NORMALIZATION -- R32/R33'te kullanilan, BYTE-EXACT (originalSha256/anchor/replacement AYNI). ----
const MTIME_ANCHOR = `        // copy outfile name into main options, as the computeScriptAndSignUninstaller function was kind enough to add important data to temporary defines.
        defines.UNINSTALLER_OUT_FILE = definesUninstaller.UNINSTALLER_OUT_FILE;
        await this.executeMakensis(defines, commands, sharedHeader + (await this.computeFinalScript(script, true, archs)));`;

const MTIME_PATCHED_MARKER = "GELKA-NSIS-MTIME-NORMALIZATION-R32";

const MTIME_REPLACEMENT = `        // copy outfile name into main options, as the computeScriptAndSignUninstaller function was kind enough to add important data to temporary defines.
        defines.UNINSTALLER_OUT_FILE = definesUninstaller.UNINSTALLER_OUT_FILE;
        // ${MTIME_PATCHED_MARKER}: NIHAI executeMakensis() (INSTALLER
        // makensis) cagrisindan HEMEN ONCE -- uninstaller URETILDI ve (varsa)
        // signing TAMAMLANDIKTAN SONRA -- build-generated girdilerin (APP_64/
        // APP_ARM64/APP_32 + UNINSTALLER_OUT_FILE) SADECE LastWriteTime'ini
        // SOURCE_DATE_EPOCH'a normalize eder. CreationTime VE LastAccessTime'a
        // DOKUNULMAZ. Icerik hash/size DEGISMEDIGI dogrulanir. Normalization
        // receipt JSON olarak durable yazilir.
        require(${JSON.stringify(path.join(__dirname, "nsis-mtime-normalization-core.js"))}).gelkaNormalizeNsisInputMtimesR32(defines);
        await this.executeMakensis(defines, commands, sharedHeader + (await this.computeFinalScript(script, true, archs)));`;

function sha256(content) { return crypto.createHash("sha256").update(content, "utf8").digest("hex"); }
function fail(msg) { console.error(`HATA [patch-nsis-order-and-mtime-r44]: ${msg}`); process.exit(1); }

function checkVersion() {
    const pkgPath = path.join(NODE_MODULES, "app-builder-lib", "package.json");
    if (!fs.existsSync(pkgPath)) fail(`Beklenen paket bulunamadi: ${pkgPath} (npm install calistirildi mi?)`);
    const pkg = JSON.parse(fs.readFileSync(pkgPath, "utf8"));
    if (pkg.version !== EXPECTED_APP_BUILDER_LIB_VERSION) {
        fail(`app-builder-lib versiyonu beklenmiyor: bulunan=${pkg.version} beklenen=${EXPECTED_APP_BUILDER_LIB_VERSION}. Patch anchor'lari yeniden dogrulanmadan uygulanmayacak (fail-closed).`);
    }
}

function apply() {
    if (!fs.existsSync(NSIS_TARGET_FILE)) fail(`Hedef dosya bulunamadi: ${NSIS_TARGET_FILE}`);
    const original = fs.readFileSync(NSIS_TARGET_FILE, "utf8");

    const hasOrderMarker = original.includes(ORDER_PATCHED_MARKER);
    const hasMtimeMarker = original.includes(MTIME_PATCHED_MARKER);
    if (hasOrderMarker && hasMtimeMarker) {
        console.log("[patch-nsis-order-and-mtime-r44] Her iki patch de ZATEN uygulanmis (idempotent), atlaniyor.");
        return { alreadyPatched: true };
    }
    if (hasOrderMarker !== hasMtimeMarker) {
        fail(`INCONSISTENT_PARTIAL_STATE: order-marker=${hasOrderMarker} mtime-marker=${hasMtimeMarker} -- dosya BEKLENMEYEN bir kismi-patch'li durumda, GUVENLI DEGIL, DURULUYOR.`);
    }

    const currentHash = sha256(original);
    if (currentHash !== PRISTINE_SHA256) {
        fail(`Dosya hash'i beklenen PRISTINE hash ile eslesmiyor (bulunan=${currentHash}, beklenen=${PRISTINE_SHA256}). Dosya degismis olabilir -- patch guvenli degil, DURULUYOR.`);
    }

    // ---- A) order patch (bellekte) ----
    const orderOccurrences = original.split(ORDER_ANCHOR).length - 1;
    if (orderOccurrences !== 1) fail(`ORDER anchor TAM 1 kez bulunmadi (bulunan=${orderOccurrences}).`);
    const afterOrder = original.split(ORDER_ANCHOR).join(ORDER_REPLACEMENT);

    // ---- B) mtime patch (bellekte, A'nin SONUCU uzerinde -- disjoint bolge, degismemis olmali) ----
    const mtimeOccurrences = afterOrder.split(MTIME_ANCHOR).length - 1;
    if (mtimeOccurrences !== 1) fail(`MTIME anchor (order-patch SONRASI icerikte) TAM 1 kez bulunmadi (bulunan=${mtimeOccurrences}) -- iki patch'in BIRBIRINI ETKILEMEDIGI varsayimi DOGRULANAMADI.`);
    const final = afterOrder.split(MTIME_ANCHOR).join(MTIME_REPLACEMENT);

    // ---- TEK atomik yazma ----
    const tmp = NSIS_TARGET_FILE + ".tmp." + process.pid;
    fs.writeFileSync(tmp, final, "utf8");
    fs.renameSync(tmp, NSIS_TARGET_FILE);
    const newHash = sha256(final);
    console.log(`[patch-nsis-order-and-mtime-r44] Her iki patch de basariyla uygulandi (yeni hash=${newHash}).`);
    return { alreadyPatched: false, newHash };
}

function main() {
    checkVersion();
    apply();
    console.log("[patch-nsis-order-and-mtime-r44] TAMAMLANDI.");
}
if (require.main === module) main();
module.exports = { apply, checkVersion, ORDER_ANCHOR, ORDER_REPLACEMENT, ORDER_PATCHED_MARKER, MTIME_ANCHOR, MTIME_REPLACEMENT, MTIME_PATCHED_MARKER, PRISTINE_SHA256, sha256 };
