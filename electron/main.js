const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');
const dbRouting = require('./dbRouting'); // PDSMR-R2: canonical DATABASE_URL karari
const { parseGateRefusal } = require('./gateRefusalParser'); // PDSMR-R3 STEP 8

let mainWindow;
let backendProcess;

const BACKEND_PORT = 8000;
const isDev = !app.isPackaged;

// PDSMR-R3 STEP 8 — backend/run_server.py, sema kapisi HARD_STOP verdiginde
// stderr'e sabit onekli, sanitize edilmis TEK bir satir yazar (bkz.
// run_server.py::_run_startup_schema_gate, gateRefusalParser.js). Bu,
// ambiguous 3-saniyelik health-check-retry dansindan GECMEDEN, dogrudan
// spesifik ve eyleme donusturulebilir bir hata gostermemizi saglar -
// backend bu durumda PORTU HIC BAGLAMAMIS olur (gate, app.main
// import'undan/uvicorn.run'dan ONCE calisir).
let lastGateRefusal = null;

// ── Backend log dosyası (crash debug için) ───────────────────────────────────
function getBackendLogPath() {
  const logDir = isDev
    ? path.join(__dirname, '..', 'backend')
    : path.join(app.getPath('userData'), 'logs');
  if (!fs.existsSync(logDir)) {
    fs.mkdirSync(logDir, { recursive: true });
  }
  return path.join(logDir, 'backend.log');
}

let backendLogStream = null;

function logBackend(msg) {
  const line = `[${new Date().toISOString()}] ${msg}\n`;
  console.log(`[backend] ${msg}`);
  if (backendLogStream) {
    backendLogStream.write(line);
  }
}

// ── Machine-local external config (secret'lar için) ──────────────────────────
// GÜVENLİK: OPENAI_API_KEY gibi secret'lar ne git'e commit edilir, ne
// installer artifact'ına (backend/.env.production → resources/backend/.env)
// gömülür — o dosya her kurulum/güncellemede ÜSTÜNE YAZILIR ve installer'ın
// kendisi paylaşılabilir bir dosyadır. Bunun yerine, kullanıcının kendi
// makinesinde userData altında duran (kurulum/güncellemeden ETKİLENMEYEN) bu
// dosyadan okunur ve backend process'inin env'ine merge edilir. Backend
// (pydantic-settings BaseSettings) gerçek ortam değişkenlerini .env dosyasından
// HER ZAMAN önceliklendirir, o yüzden Python tarafında hiçbir değişiklik
// gerekmiyor — mevcut `env: {...process.env}` spawn deseni genişletiliyor.
function loadMachineLocalEnv() {
  const envPath = path.join(app.getPath('userData'), 'machine-local.env');
  const extra = {};
  try {
    if (fs.existsSync(envPath)) {
      const content = fs.readFileSync(envPath, 'utf-8');
      for (const rawLine of content.split(/\r?\n/)) {
        const line = rawLine.trim();
        if (!line || line.startsWith('#')) continue;
        const eqIdx = line.indexOf('=');
        if (eqIdx === -1) continue;
        const key = line.slice(0, eqIdx).trim();
        const value = line.slice(eqIdx + 1).trim();
        if (key) extra[key] = value;
      }
      logBackend(`Machine-local config yüklendi: ${Object.keys(extra).length} değişken (${envPath})`);
    }
  } catch (e) {
    logBackend(`Machine-local config okunamadı (${envPath}): ${e.message}`);
  }
  return extra;
}

// ── Backend lifecycle ────────────────────────────────────────────────────────

function waitForBackend(retries = 60) {
  return new Promise((resolve, reject) => {
    const check = (attempt) => {
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else if (attempt < retries) {
          setTimeout(() => check(attempt + 1), 1000);
        } else {
          reject(new Error('Backend başlatılamadı'));
        }
      });
      req.on('error', () => {
        if (attempt < retries) {
          setTimeout(() => check(attempt + 1), 1000);
        } else {
          reject(new Error('Backend bağlantısı kurulamadı'));
        }
      });
      req.setTimeout(2000);
    };
    check(0);
  });
}

function startBackend() {
  // Log dosyasını aç
  try {
    backendLogStream = fs.createWriteStream(getBackendLogPath(), { flags: 'a' });
    logBackend('--- Backend starting ---');
    logBackend(`isDev=${isDev}, resourcesPath=${isDev ? 'N/A' : process.resourcesPath}`);
  } catch (e) {
    console.error('Log dosyası açılamadı:', e);
  }

  const machineLocalEnv = loadMachineLocalEnv();

  if (isDev) {
    const pythonPath = process.platform === 'win32' ? 'python' : 'python3';
    backendProcess = spawn(pythonPath,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      { cwd: path.join(__dirname, '..', 'backend'), env: { ...process.env, ...machineLocalEnv }, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  } else {
    // PDSMR-R2: paketli backend YALNIZ canonical (userData/database) DB'yi
    // kullanmalı. resources/backend/gelka_enerji.db (legacy) installer
    // upgrade sırasında SİLİNEBİLİR; DATABASE_URL burada AÇIKÇA verilmezse
    // backend'in kendi setdefault fallback'i (run_server.py) legacy yola
    // düşer ve upgrade sonrası SESSİZCE yeni boş bir DB açılabilir.
    const routing = dbRouting.resolveDatabaseRouting({
      userDataDir: app.getPath('userData'),
      resourcesPath: process.resourcesPath,
    });
    logBackend(`DB routing: mode=${routing.mode} reason="${routing.reason}"`);

    if (routing.mode === dbRouting.MODE_FAIL_CLOSED_MISSING_RESCUE) {
      // legacy VAR, canonical YOK: pre-upgrade kurtarma (PDSMR-R2) hiç
      // çalışmamış ya da başarısız olmuş olabilir. Sessizce devam edip
      // resources altında yeni bir DB açmak veri kaybını gizler —
      // FAIL CLOSED: backend hiç başlatılmaz, kullanıcı açıkça bilgilendirilir.
      logBackend(`[FAIL_CLOSED] legacy=${routing.legacyPath} canonical=${routing.canonicalPath}`);
      dialog.showErrorBox(
        'Veritabanı Bulunamadı',
        'Kurulum güncellemesi sırasında veri taşıma adımı tamamlanamamış görünüyor.\n' +
        'Mevcut verileriniz kaybolmadı ancak uygulama güvenlik nedeniyle başlatılmıyor.\n' +
        'Lütfen destek ile iletişime geçin.\n\n' +
        `Log: ${getBackendLogPath()}`
      );
      return;
    }

    if (!fs.existsSync(path.dirname(routing.canonicalPath))) {
      fs.mkdirSync(path.dirname(routing.canonicalPath), { recursive: true });
    }

    // S5-R03B: durable PDF/belge storage kökü — DB ile SIMETRIK olarak
    // userData altında (`<userData>/storage`). resources/backend/storage
    // (eski/CWD-göreli varsayılan) upgrade sırasında installer'ın
    // `uninstallOldVersion` adımıyla SESSİZCE silinir (bkz. installer.nsh);
    // rescue/backup mekanizması yalnız .db'yi kapsar, storage'ı KAPSAMAZ.
    // Dizin burada best-effort oluşturulur — GERÇEK fail-closed containment/
    // reparse-point denetimi backend tarafında (LocalStorage.__init__,
    // app/services/storage_local.py) yapılır; Electron'un mkdir'i başarısız
    // olsa bile backend kendi mkdir'ini dener ve gerekirse orada patlar.
    const durableStorageDir = dbRouting.resolveDurableStorageDir(app.getPath('userData'));
    try {
      if (!fs.existsSync(durableStorageDir)) {
        fs.mkdirSync(durableStorageDir, { recursive: true });
      }
    } catch (e) {
      logBackend(`[UYARI] durable storage dizini oluşturulamadı: ${e.message}`);
    }

    const backendDir = path.join(process.resourcesPath, 'backend');
    const backendExe = path.join(backendDir, 'gelka-backend.exe');
    logBackend(`Backend exe: ${backendExe}`);
    logBackend(`Backend dir: ${backendDir}`);
    logBackend(`Exe exists: ${fs.existsSync(backendExe)}`);
    logBackend(`DATABASE_URL: ${routing.url}`);
    logBackend(`STORAGE_DIR: ${durableStorageDir}`);
    backendProcess = spawn(backendExe,
      ['--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      {
        cwd: backendDir,
        // PLAYWRIGHT_BROWSERS_PATH=0: yalnız packaged'da — Chromium'u kullanıcı
        // cache'inden (%LOCALAPPDATA%\ms-playwright) DEĞİL, PyInstaller build'inin
        // exe içine gömdüğü paket-göreli konumdan (playwright/driver/package/
        // .local-browsers) kullan. Dev branch'ine BİLEREK eklenmedi — dev ortamı
        // hâlâ kullanıcı cache'ini kullanmaya devam eder (regresyon yok).
        //
        // KORUNAN ANAHTARLAR (PDSMR-R3 STEP 2, PDSMR-R3B STEP 5, S5-R03B):
        // DATABASE_URL, ENV, GELKA_PACKAGED_RUNTIME, STORAGE_DIR — dördü de
        // ...machineLocalEnv'DEN SONRA literal olarak verilir, böylece
        // machine-local.env (kullanıcının kendi makinesinde, elle
        // düzenlenebilir bir dosya) bunları ASLA EZEMEZ. DATABASE_URL zaten
        // böyleydi (run_server.py os.environ.setdefault kullanır);
        // ENV/GELKA_PACKAGED_RUNTIME de AYNI ilkeyle eklendi —
        // backend/app/database.py::init_db() GELKA_PACKAGED_RUNTIME'ı
        // create_all() SESSİZ-YOK-SAYMA (fail-open, PDSMR-R2I bulgusu)
        // sinyali olarak KOŞULSUZ kabul eder. STORAGE_DIR de AYNI korumayı
        // alır — aksi hâlde machine-local.env'e elle yazılan keyfi bir
        // STORAGE_DIR, containment kökünü kullanıcı kontrolüne bırakırdı
        // (S5-R03B Bölüm 6: "kalıcı storage kökü tahmin edilerek
        // seçilmeyecek" ilkesiyle çelişirdi).
        // ENV='desktop' (PDSMR-R3B STEP 5 — ÖNCEDEN 'staging' idi, YANILTICIYDI):
        // paketlenmiş masaüstü uygulamasının GERÇEK/DÜRÜST değeri — bkz.
        // backend/.env.production ve incident_service.py::VALID_ENVIRONMENTS
        // yorumları. run_server.py bu İKİ değeri (ENV + GELKA_PACKAGED_RUNTIME)
        // AYRICA, Electron'dan BAĞIMSIZ olarak doğrular (fail-closed).
        env: {
          ...process.env, ...machineLocalEnv,
          PLAYWRIGHT_BROWSERS_PATH: '0',
          DATABASE_URL: routing.url,
          ENV: 'desktop',
          GELKA_PACKAGED_RUNTIME: '1',
          STORAGE_DIR: durableStorageDir,
        },
        stdio: ['pipe', 'pipe', 'pipe'],
      }
    );
  }

  lastGateRefusal = null;
  backendProcess.stdout.on('data', (data) => logBackend(`[stdout] ${data.toString().trim()}`));
  backendProcess.stderr.on('data', (data) => {
    const metin = data.toString();
    logBackend(`[stderr] ${metin.trim()}`);
    const ayristirilan = parseGateRefusal(metin);
    if (ayristirilan) {
      lastGateRefusal = ayristirilan;
    }
  });
  backendProcess.on('error', (err) => {
    logBackend(`[ERROR] Backend başlatma hatası: ${err.message}`);
    dialog.showErrorBox('Hata', `Backend başlatılamadı: ${err.message}`);
  });
  backendProcess.on('exit', (code, signal) => {
    logBackend(`[EXIT] Backend process kapandı (code: ${code}, signal: ${signal})`);
    backendProcess = null;

    // PDSMR-R3 STEP 8: sema kapisi ACIKCA reddettiyse (stderr'de yakalanan
    // sabit onek), belirsiz health-check-retry dansina GEREK YOK -
    // backend PORTU HIC BAGLAMADI (gate, uvicorn.run'dan ONCE calisti).
    // Sanitize edilmis, eyleme donusturulebilir mesaji DOGRUDAN goster.
    // "Devam et" secenegi KASITLI olarak YOK (owner: "never offer
    // continue anyway").
    if (lastGateRefusal) {
      const { exitCode, message } = lastGateRefusal;
      logBackend(`[GATE_REFUSED] exitCode=${exitCode} message="${message}"`);
      if (mainWindow && !mainWindow.isDestroyed()) {
        dialog.showErrorBox(
          'Veritabanı Hazırlanamadı',
          'Uygulama başlangıcı, veritabanı durumu güvenli şekilde ' +
          'doğrulanamadığı için güvenlik nedeniyle durduruldu.\n' +
          'Mevcut verileriniz DEĞİŞTİRİLMEDİ.\n\n' +
          `Kategori: ${exitCode !== null ? exitCode : 'beklenmeyen hata'}\n` +
          'Lütfen destek ile iletişime geçin.\n\n' +
          `Log: ${getBackendLogPath()}`
        );
      }
      return;
    }

    // PyInstaller --onefile modunda wrapper process kapanabilir ama
    // asıl Python process hala çalışıyor olabilir.
    // Health check yaparak gerçek durumu kontrol et.
    // NOT: code=1 olsa bile backend çalışıyor olabilir (ikinci worker crash'i gibi)
    if (code !== 0 && code !== null) {
      setTimeout(() => {
        const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
          if (res.statusCode === 200) {
            logBackend('[EXIT] Backend hala çalışıyor (health OK). Hata yok sayılıyor.');
          } else {
            logBackend(`[EXIT] Backend health check failed: status=${res.statusCode}`);
            // Sadece pencere hala açıksa hata göster
            if (mainWindow && !mainWindow.isDestroyed()) {
              dialog.showErrorBox('Backend Hatası',
                `Backend beklenmedik şekilde kapandı (code: ${code}).\nLog: ${getBackendLogPath()}`);
            }
          }
        });
        req.on('error', () => {
          logBackend('[EXIT] Backend gerçekten kapanmış (health unreachable).');
          if (mainWindow && !mainWindow.isDestroyed()) {
            dialog.showErrorBox('Backend Hatası',
              `Backend beklenmedik şekilde kapandı (code: ${code}).\nLog: ${getBackendLogPath()}`);
          }
        });
        req.setTimeout(3000);
      }, 3000); // 3 saniye bekle — PyInstaller extraction süresi
    }
  });
}

function stopBackend() {
  logBackend('Stopping backend...');
  if (backendProcess) {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
  // PyInstaller --onefile: wrapper process kapanmış olabilir ama
  // asıl Python process hala port'u dinliyor olabilir.
  // Port üzerinden de temizle.
  if (process.platform === 'win32') {
    try {
      const { execSync } = require('child_process');
      const result = execSync(
        `netstat -ano | findstr ":${BACKEND_PORT}" | findstr "LISTENING"`,
        { encoding: 'utf-8', timeout: 5000 }
      ).trim();
      const lines = result.split('\n');
      for (const line of lines) {
        const parts = line.trim().split(/\s+/);
        const pid = parts[parts.length - 1];
        if (pid && /^\d+$/.test(pid)) {
          logBackend(`Killing leftover backend process PID=${pid}`);
          try { execSync(`taskkill /pid ${pid} /f /t`, { timeout: 5000 }); } catch {}
        }
      }
    } catch {
      // Port'ta dinleyen process yok, sorun değil
    }
  }
  if (backendLogStream) {
    backendLogStream.end();
    backendLogStream = null;
  }
}


// ── IPC: PDF Download (main process ile dosya indirme) ───────────────────────

// Güvenlik: İzin verilen backend adresi (sadece loopback IP, localhost DNS resolve riski nedeniyle yok)
const ALLOWED_DOWNLOAD_ORIGINS = [
  `http://127.0.0.1:${BACKEND_PORT}`,
  `http://localhost:${BACKEND_PORT}`,
];
// İzin verilen path prefix'leri (sadece PDF endpoint'leri) — S5-R03D: bu
// liste GENİŞLETİLMEZ (legacy /generate-pdf-simple ve /api/contracts aynen
// kalır). Teklif PDF indirme (offer-bound) için AŞAĞIDAKİ EXACT route
// matcher kullanılır — asla genel bir '/offers/' prefix'i DEĞİL.
const ALLOWED_PATH_PREFIXES = ['/generate-pdf', '/api/contracts'];
const MAX_PDF_SIZE = 50 * 1024 * 1024; // 50MB hard limit

// S5-R03D — Authoritative backend route: GET /offers/{offer_id}/download
// (backend/app/main.py, @app.get("/offers/{offer_id}/download"), offer_id: int).
// Starlette'in int path-converter'ı `[0-9]+` kullanır (negatif/non-numeric
// zaten route seviyesinde reddedilir); frontend (api.ts::downloadOfferPdf)
// HİÇBİR ZAMAN query/fragment eklemez — `${API_BASE}/offers/${offerId}/download`.
// `[1-9]\d*` ile "0" ve lider-sıfır da reddedilir (pratikte hiç oluşmaz,
// yalnız ekstra güvenlik marjı). EXACT string eşleşmesi (^...$) — suffix,
// alt-rota veya genişletilmiş prefix KABUL EDİLMEZ.
const OFFER_DOWNLOAD_PATH_MATCHER = /^\/offers\/[1-9]\d*\/download$/;

/**
 * URL'in güvenli olduğunu doğrula.
 * Kontroller: parse, protocol, username/password, origin allowlist,
 * query/fragment yokluğu, path allowlist (prefix VEYA exact offer-download).
 */
function validateDownloadUrl(rawUrl) {
  let parsed;
  try {
    parsed = new URL(rawUrl);
  } catch {
    return { ok: false, error: 'Geçersiz URL formatı.' };
  }

  // Protokol: sadece http
  if (parsed.protocol !== 'http:') {
    return { ok: false, error: `Güvenli olmayan protokol: ${parsed.protocol}` };
  }

  // Basic auth trick engeli: http://user:pass@127.0.0.1:8000
  if (parsed.username || parsed.password) {
    return { ok: false, error: 'URL içinde kimlik bilgisi yasak.' };
  }

  // Origin allowlist (host + port)
  const origin = parsed.origin;
  if (!ALLOWED_DOWNLOAD_ORIGINS.includes(origin)) {
    return { ok: false, error: `İzin verilmeyen adres: ${origin}` };
  }

  // Path allowlist: mevcut prefix'ler (legacy generate-pdf + contracts) VEYA
  // EXACT offer-download route eşleşmesi. `pathname` query/fragment İÇERMEZ
  // (URL nesnesi bunları ayrı tutar) — o yüzden "query/fragment yok" şartı
  // AYRICA, aşağıda açıkça kontrol edilir.
  const pathAllowed =
    ALLOWED_PATH_PREFIXES.some(prefix => parsed.pathname.startsWith(prefix)) ||
    OFFER_DOWNLOAD_PATH_MATCHER.test(parsed.pathname);
  if (!pathAllowed) {
    return { ok: false, error: `İzin verilmeyen path: ${parsed.pathname}` };
  }

  // Query/fragment yasak (yalnız offer-download route'u için değil, TÜM
  // indirme URL'leri için sıkı sözleşme — mevcut çağıranların hiçbiri
  // query/fragment kullanmaz, bu bir davranış değişikliği YARATMAZ).
  if (parsed.search) {
    return { ok: false, error: 'URL içinde query string yasak.' };
  }
  if (parsed.hash) {
    return { ok: false, error: 'URL içinde fragment yasak.' };
  }

  return { ok: true, parsed };
}

ipcMain.handle('download:pdf', async (event, { url, formData, fileName }) => {
  // ── 0) localhost → 127.0.0.1 normalize (net.request localhost sorununu önler) ──
  let normalizedUrl = url;
  try {
    const u = new URL(url);
    if (u.hostname === 'localhost') {
      u.hostname = '127.0.0.1';
      normalizedUrl = u.toString();
    }
  } catch { /* validateDownloadUrl yakalayacak */ }

  // ── 1) URL doğrulama (SSRF koruması) ──
  const urlCheck = validateDownloadUrl(normalizedUrl);
  if (!urlCheck.ok) {
    console.error(`[download:pdf] URL reddedildi: ${urlCheck.error} (url=${normalizedUrl})`);
    return { ok: false, error: urlCheck.error };
  }

  // ── 2) formData doğrulama ──
  if (!formData || typeof formData !== 'object') {
    return { ok: false, error: 'Geçersiz form verisi.' };
  }

  // ── 3) fileName sanitize ──
  const safeName = (fileName || 'teklif.pdf')
    .replace(/[/\\:*?"<>|]/g, '_')  // Tehlikeli karakterleri temizle
    .replace(/\.\./g, '_');          // Path traversal engelle

  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return { ok: false, error: 'Pencere bulunamadı.' };

  // ── 4) Kullanıcıya "Farklı Kaydet" dialogu göster ──
  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    defaultPath: path.join(app.getPath('desktop'), safeName),
    filters: [{ name: 'PDF Dosyası', extensions: ['pdf'] }],
  });
  if (canceled || !filePath) return { ok: false, canceled: true };

  // ── 5) multipart/form-data body oluştur (boundary injection korumalı) ──
  const boundary = '----ElectronBoundary' + require('crypto').randomBytes(16).toString('hex');
  let body = '';
  for (const [key, value] of Object.entries(formData)) {
    // Key ve value'dan boundary string'ini temizle
    const safeKey = String(key).replace(/[\r\n"]/g, '');
    const safeValue = String(value).replace(new RegExp(`--${boundary}`, 'g'), '');
    body += `--${boundary}\r\n`;
    body += `Content-Disposition: form-data; name="${safeKey}"\r\n\r\n`;
    body += `${safeValue}\r\n`;
  }
  body += `--${boundary}--\r\n`;
  const bodyBuffer = Buffer.from(body, 'utf-8');

  // ── 6) HTTP request (Node.js native http modülü) ──
  return new Promise((resolve) => {
    const parsedUrl = new URL(normalizedUrl);
    const options = {
      hostname: parsedUrl.hostname,
      port: parsedUrl.port,
      path: parsedUrl.pathname + parsedUrl.search,
      method: 'POST',
      headers: {
        'Content-Type': `multipart/form-data; boundary=${boundary}`,
        'Content-Length': String(bodyBuffer.length),
      },
    };

    const request = http.request(options, (response) => {
      const statusCode = response.statusCode;
      const responseContentType = (response.headers['content-type'] || '').toString();
      console.log(`[download:pdf] Response: status=${statusCode}, content-type=${responseContentType}`);

      const chunks = [];
      let totalBytes = 0;

      response.on('data', (chunk) => {
        totalBytes += chunk.length;
        if (totalBytes > MAX_PDF_SIZE) {
          request.destroy();
          resolve({ ok: false, error: `PDF boyutu limiti aşıldı (>${MAX_PDF_SIZE / 1024 / 1024}MB).` });
          return;
        }
        chunks.push(chunk);
      });

      response.on('end', () => {
        const buffer = Buffer.concat(chunks);

        // HTTP hata kontrolü
        if (statusCode !== 200) {
          let errorResult = { ok: false, statusCode, error: `Sunucu hatası (${statusCode})` };
          if (responseContentType.includes('application/json')) {
            try {
              const parsed = JSON.parse(buffer.toString('utf-8'));
              const errObj = parsed.error || parsed;
              errorResult.code = errObj.code || 'unknown';
              errorResult.error = errObj.message || errObj.detail || errorResult.error;
              errorResult.request_id = errObj.request_id || null;
              if (errObj.code === 'extraction_mismatch') {
                errorResult.mismatch = errObj;  // R2: mismatch contract'ini renderer'a tasi
              }
              if (statusCode === 429) {
                const retryAfter = (response.headers['retry-after'] || '').toString();
                errorResult.retry_after = parseInt(retryAfter, 10) || 5;
                errorResult.error = `Sunucu meşgul. Lütfen ${errorResult.retry_after} saniye bekleyin.`;
              }
            } catch (parseErr) {
              errorResult.error = buffer.toString('utf-8').slice(0, 500);
            }
          } else {
            errorResult.error = buffer.toString('utf-8').slice(0, 500) || errorResult.error;
          }
          console.error(`[download:pdf] Sunucu hatası (${statusCode}): ${errorResult.error}`);
          resolve(errorResult);
          return;
        }

        // Boş response kontrolü
        if (buffer.length === 0) {
          resolve({ ok: false, error: 'Sunucudan boş PDF yanıtı alındı.' });
          return;
        }

        // Content-Type kontrolü
        if (!responseContentType.includes('application/pdf')) {
          console.error(`[download:pdf] Beklenmeyen content-type: ${responseContentType}`);
          if (responseContentType.includes('application/json')) {
            try {
              const parsed = JSON.parse(buffer.toString('utf-8'));
              const errObj = parsed.error || parsed;
              resolve({ ok: false, code: errObj.code, error: errObj.message || errObj.detail || 'Bilinmeyen hata', request_id: errObj.request_id });
            } catch {
              resolve({ ok: false, error: buffer.toString('utf-8').slice(0, 500) });
            }
          } else {
            resolve({ ok: false, error: `Beklenmeyen yanıt tipi: ${responseContentType}` });
          }
          return;
        }

        // PDF magic bytes kontrolü (%PDF-)
        if (buffer.length >= 5 && buffer.toString('ascii', 0, 5) !== '%PDF-') {
          console.error('[download:pdf] Dosya PDF formatında değil (magic bytes uyumsuz).');
          resolve({ ok: false, error: 'İndirilen dosya geçerli bir PDF değil.' });
          return;
        }

        // Dosyaya yaz
        fs.writeFile(filePath, buffer, (err) => {
          if (err) {
            console.error(`[download:pdf] Dosya yazma hatası: ${err.message}`);
            resolve({ ok: false, error: `Dosya kaydedilemedi: ${err.message}` });
          } else {
            console.log(`[download:pdf] PDF kaydedildi: ${filePath} (${buffer.length} bytes)`);
            // Otomatik aç - masaüstüne kaydedildiğinde hemen kontrol edilebilsin
            shell.openPath(filePath).then((openErr) => {
              if (openErr) {
                console.warn(`[download:pdf] PDF otomatik açılamadı: ${openErr}`);
              }
            });
            resolve({ ok: true, filePath });
          }
        });
      });
    });

    request.on('error', (err) => {
      console.error(`[download:pdf] İstek hatası: ${err.message}`);
      resolve({ ok: false, error: `Bağlantı hatası: ${err.message}` });
    });

    request.write(bodyBuffer);
    request.end();
  });
});

// ── IPC: Basit GET tabanlı dosya indirme (sözleşme PDF'i — download:pdf'in
// POST+multipart body'sine ihtiyacı yok, tek fark budur; doğrulama/kaydetme
// akışı birebir aynı) ──────────────────────────────────────────────────────

ipcMain.handle('download:file', async (event, { url, fileName }) => {
  let normalizedUrl = url;
  try {
    const u = new URL(url);
    if (u.hostname === 'localhost') {
      u.hostname = '127.0.0.1';
      normalizedUrl = u.toString();
    }
  } catch { /* validateDownloadUrl yakalayacak */ }

  const urlCheck = validateDownloadUrl(normalizedUrl);
  if (!urlCheck.ok) {
    console.error(`[download:file] URL reddedildi: ${urlCheck.error} (url=${normalizedUrl})`);
    return { ok: false, error: urlCheck.error };
  }

  const safeName = (fileName || 'dosya.pdf')
    .replace(/[/\\:*?"<>|]/g, '_')
    .replace(/\.\./g, '_');

  const win = BrowserWindow.fromWebContents(event.sender);
  if (!win) return { ok: false, error: 'Pencere bulunamadı.' };

  const { canceled, filePath } = await dialog.showSaveDialog(win, {
    defaultPath: path.join(app.getPath('desktop'), safeName),
    filters: [{ name: 'PDF Dosyası', extensions: ['pdf'] }],
  });
  if (canceled || !filePath) return { ok: false, canceled: true };

  return new Promise((resolve) => {
    const parsedUrl = new URL(normalizedUrl);
    const options = {
      hostname: parsedUrl.hostname,
      port: parsedUrl.port,
      path: parsedUrl.pathname + parsedUrl.search,
      method: 'GET',
    };

    const request = http.request(options, (response) => {
      const statusCode = response.statusCode;
      const responseContentType = (response.headers['content-type'] || '').toString();

      const chunks = [];
      let totalBytes = 0;

      response.on('data', (chunk) => {
        totalBytes += chunk.length;
        if (totalBytes > MAX_PDF_SIZE) {
          request.destroy();
          resolve({ ok: false, error: `Dosya boyutu limiti aşıldı (>${MAX_PDF_SIZE / 1024 / 1024}MB).` });
          return;
        }
        chunks.push(chunk);
      });

      response.on('end', () => {
        const buffer = Buffer.concat(chunks);

        if (statusCode !== 200) {
          let errorResult = { ok: false, statusCode, error: `Sunucu hatası (${statusCode})` };
          if (responseContentType.includes('application/json')) {
            try {
              const parsed = JSON.parse(buffer.toString('utf-8'));
              const errObj = parsed.detail || parsed;
              errorResult.error = errObj.message || errObj.detail || errorResult.error;
            } catch {
              errorResult.error = buffer.toString('utf-8').slice(0, 500);
            }
          }
          console.error(`[download:file] Sunucu hatası (${statusCode}): ${errorResult.error}`);
          resolve(errorResult);
          return;
        }

        if (buffer.length === 0) {
          resolve({ ok: false, error: 'Sunucudan boş yanıt alındı.' });
          return;
        }

        if (buffer.length >= 5 && buffer.toString('ascii', 0, 5) !== '%PDF-') {
          console.error('[download:file] Dosya PDF formatında değil (magic bytes uyumsuz).');
          resolve({ ok: false, error: 'İndirilen dosya geçerli bir PDF değil.' });
          return;
        }

        fs.writeFile(filePath, buffer, (err) => {
          if (err) {
            console.error(`[download:file] Dosya yazma hatası: ${err.message}`);
            resolve({ ok: false, error: `Dosya kaydedilemedi: ${err.message}` });
          } else {
            console.log(`[download:file] Dosya kaydedildi: ${filePath} (${buffer.length} bytes)`);
            shell.openPath(filePath).then((openErr) => {
              if (openErr) console.warn(`[download:file] Dosya otomatik açılamadı: ${openErr}`);
            });
            resolve({ ok: true, filePath });
          }
        });
      });
    });

    request.on('error', (err) => {
      console.error(`[download:file] İstek hatası: ${err.message}`);
      resolve({ ok: false, error: `Bağlantı hatası: ${err.message}` });
    });

    request.end();
  });
});

// ── Window ───────────────────────────────────────────────────────────────────

async function createWindow() {
  mainWindow = new BrowserWindow({
    width: 1400,
    height: 900,
    minWidth: 1024,
    minHeight: 700,
    title: 'Gelka Enerji',
    icon: undefined,
    webPreferences: {
      nodeIntegration: false,
      contextIsolation: true,
      sandbox: true,
      webSecurity: true,
      allowRunningInsecureContent: false,
      preload: path.join(__dirname, 'preload.js'),
    },
    show: false,
  });

  mainWindow.once('ready-to-show', () => mainWindow.show());

  if (isDev) {
    mainWindow.loadURL('http://localhost:3000').catch(() => {
      mainWindow.loadURL(`data:text/html;charset=utf-8,${encodeURIComponent(`
        <!DOCTYPE html>
        <html><head><meta charset="utf-8"><title>Gelka Enerji - Dev Server Bekleniyor</title>
        <style>
          body { font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif; display: flex; align-items: center; justify-content: center; height: 100vh; margin: 0; background: #f8fafc; color: #334155; }
          .box { text-align: center; max-width: 480px; padding: 2rem; }
          h2 { color: #0f172a; margin-bottom: 0.5rem; }
          code { background: #e2e8f0; padding: 2px 8px; border-radius: 4px; font-size: 14px; }
          .steps { text-align: left; margin-top: 1rem; line-height: 1.8; }
          button { margin-top: 1rem; padding: 8px 24px; background: #2563eb; color: white; border: none; border-radius: 6px; cursor: pointer; font-size: 14px; }
          button:hover { background: #1d4ed8; }
        </style></head>
        <body><div class="box">
          <h2>⚡ Frontend Dev Server Çalışmıyor</h2>
          <p>Electron, <code>http://localhost:3000</code> adresine bağlanamadı.</p>
          <div class="steps">
            <strong>Çözüm:</strong><br>
            1. <code>frontend/</code> klasöründe terminali açın<br>
            2. <code>npm run dev</code> komutunu çalıştırın<br>
            3. "Local: http://localhost:3000" mesajını bekleyin<br>
            4. Aşağıdaki butona tıklayın
          </div>
          <button onclick="window.location.href='http://localhost:3000'">Tekrar Dene</button>
        </div></body></html>
      `)}`);
    });
    mainWindow.webContents.openDevTools();
  } else {
    mainWindow.loadFile(path.join(process.resourcesPath, 'frontend', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => { mainWindow = null; });
}

// ── Single instance lock ──────────────────────────────────────────────────────
const gotTheLock = app.requestSingleInstanceLock();
if (!gotTheLock) {
  app.quit();
} else {
  app.on('second-instance', () => {
    // İkinci instance açılmaya çalışınca mevcut pencereyi öne getir
    if (mainWindow) {
      if (mainWindow.isMinimized()) mainWindow.restore();
      mainWindow.focus();
    }
  });
}

// ── App lifecycle ────────────────────────────────────────────────────────────

app.whenReady().then(async () => {
  startBackend();
  try {
    await waitForBackend();
    console.log('Backend hazır');
  } catch (err) {
    console.error(err.message);
    dialog.showErrorBox('Başlatma Hatası', 'Backend sunucusu başlatılamadı. Lütfen tekrar deneyin.');
  }
  await createWindow();
});

app.on('window-all-closed', () => { stopBackend(); app.quit(); });
app.on('before-quit', () => stopBackend());
app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) createWindow();
});

// ── Test edilebilirlik (S5-R03D) ──────────────────────────────────────────
// main.js gerçek Electron process'i tarafından çalıştırılır ve dosyanın
// tepesinde top-level Electron API çağrıları içerir (`!app.isPackaged`,
// `app.requestSingleInstanceLock()`) — bu yüzden düz `require('./main.js')`
// gerçek bir Electron çalışma-zamanı OLMADAN (örn. `node main.test.js`)
// bu satırlara ulaşana kadar çöker. main.test.js bunu, main.js'e HİÇBİR
// DAVRANIŞ DEĞİŞİKLİĞİ getirmeden, `electron` modülünü test-zamanlı sahte
// bir nesneyle değiştirerek (require.cache enjeksiyonu) çözer; bu export
// SADECE o test-zamanlı yüklemenin `validateDownloadUrl`'a erişebilmesi
// içindir, paketlenmiş/gerçek çalışma zamanını ETKİLEMEZ.
if (typeof module !== 'undefined' && module.exports) {
  module.exports = {
    validateDownloadUrl,
    ALLOWED_PATH_PREFIXES,
    ALLOWED_DOWNLOAD_ORIGINS,
    OFFER_DOWNLOAD_PATH_MATCHER,
  };
}
