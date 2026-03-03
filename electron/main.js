const { app, BrowserWindow, dialog, ipcMain, shell } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

let mainWindow;
let backendProcess;

const BACKEND_PORT = 8000;
const isDev = !app.isPackaged;

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

  if (isDev) {
    const pythonPath = process.platform === 'win32' ? 'python' : 'python3';
    backendProcess = spawn(pythonPath,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      { cwd: path.join(__dirname, '..', 'backend'), env: { ...process.env }, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  } else {
    const backendDir = path.join(process.resourcesPath, 'backend');
    const backendExe = path.join(backendDir, 'gelka-backend.exe');
    logBackend(`Backend exe: ${backendExe}`);
    logBackend(`Backend dir: ${backendDir}`);
    logBackend(`Exe exists: ${fs.existsSync(backendExe)}`);
    backendProcess = spawn(backendExe,
      ['--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      { cwd: backendDir, env: { ...process.env }, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  }

  backendProcess.stdout.on('data', (data) => logBackend(`[stdout] ${data.toString().trim()}`));
  backendProcess.stderr.on('data', (data) => logBackend(`[stderr] ${data.toString().trim()}`));
  backendProcess.on('error', (err) => {
    logBackend(`[ERROR] Backend başlatma hatası: ${err.message}`);
    dialog.showErrorBox('Hata', `Backend başlatılamadı: ${err.message}`);
  });
  backendProcess.on('exit', (code, signal) => {
    logBackend(`[EXIT] Backend process kapandı (code: ${code}, signal: ${signal})`);
    backendProcess = null;

    // PyInstaller --onefile modunda wrapper process kapanabilir ama
    // asıl Python process hala çalışıyor olabilir.
    // Health check yaparak gerçek durumu kontrol et.
    if (code !== 0 && code !== null) {
      setTimeout(() => {
        const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
          if (res.statusCode === 200) {
            logBackend('[EXIT] Backend hala çalışıyor (health OK). Hata yok sayılıyor.');
          } else {
            logBackend(`[EXIT] Backend health check failed: status=${res.statusCode}`);
            dialog.showErrorBox('Backend Hatası',
              `Backend beklenmedik şekilde kapandı (code: ${code}).\nLog: ${getBackendLogPath()}`);
          }
        });
        req.on('error', () => {
          logBackend('[EXIT] Backend gerçekten kapanmış (health unreachable).');
          dialog.showErrorBox('Backend Hatası',
            `Backend beklenmedik şekilde kapandı (code: ${code}).\nLog: ${getBackendLogPath()}`);
        });
        req.setTimeout(3000);
      }, 2000); // 2 saniye bekle, belki backend hala ayağa kalkıyor
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
// İzin verilen path prefix'leri (sadece PDF endpoint'leri)
const ALLOWED_PATH_PREFIXES = ['/generate-pdf'];
const MAX_PDF_SIZE = 50 * 1024 * 1024; // 50MB hard limit

/**
 * URL'in güvenli olduğunu doğrula.
 * Kontroller: parse, protocol, username/password, origin allowlist, path allowlist.
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

  // Path allowlist: sadece PDF endpoint'lerine izin ver
  const pathAllowed = ALLOWED_PATH_PREFIXES.some(prefix => parsed.pathname.startsWith(prefix));
  if (!pathAllowed) {
    return { ok: false, error: `İzin verilmeyen path: ${parsed.pathname}` };
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
