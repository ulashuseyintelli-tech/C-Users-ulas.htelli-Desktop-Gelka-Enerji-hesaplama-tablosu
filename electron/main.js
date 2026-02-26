const { app, BrowserWindow, dialog, ipcMain, net } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');
const fs = require('fs');

let mainWindow;
let backendProcess;

const BACKEND_PORT = 8000;
const isDev = !app.isPackaged;

// ── Backend lifecycle ────────────────────────────────────────────────────────

function waitForBackend(retries = 30) {
  return new Promise((resolve, reject) => {
    const check = (attempt) => {
      const req = http.get(`http://127.0.0.1:${BACKEND_PORT}/health`, (res) => {
        if (res.statusCode === 200) {
          resolve();
        } else if (attempt < retries) {
          setTimeout(() => check(attempt + 1), 500);
        } else {
          reject(new Error('Backend başlatılamadı'));
        }
      });
      req.on('error', () => {
        if (attempt < retries) {
          setTimeout(() => check(attempt + 1), 500);
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
  if (isDev) {
    const pythonPath = process.platform === 'win32' ? 'python' : 'python3';
    backendProcess = spawn(pythonPath,
      ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      { cwd: path.join(__dirname, '..', 'backend'), env: { ...process.env }, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  } else {
    const backendExe = path.join(process.resourcesPath, 'backend', 'gelka-backend.exe');
    backendProcess = spawn(backendExe,
      ['--host', '127.0.0.1', '--port', String(BACKEND_PORT)],
      { env: { ...process.env }, stdio: ['pipe', 'pipe', 'pipe'] }
    );
  }

  backendProcess.stdout.on('data', (data) => console.log(`[backend] ${data.toString().trim()}`));
  backendProcess.stderr.on('data', (data) => console.error(`[backend] ${data.toString().trim()}`));
  backendProcess.on('error', (err) => {
    console.error('Backend başlatma hatası:', err);
    dialog.showErrorBox('Hata', `Backend başlatılamadı: ${err.message}`);
  });
  backendProcess.on('exit', (code) => { console.log(`Backend kapandı (code: ${code})`); backendProcess = null; });
}

function stopBackend() {
  if (backendProcess) {
    if (process.platform === 'win32') {
      spawn('taskkill', ['/pid', String(backendProcess.pid), '/f', '/t']);
    } else {
      backendProcess.kill('SIGTERM');
    }
    backendProcess = null;
  }
}

// ── IPC: PDF Download (main process ile dosya indirme) ───────────────────────

// Güvenlik: İzin verilen backend adresi (sadece loopback IP, localhost DNS resolve riski nedeniyle yok)
const ALLOWED_DOWNLOAD_ORIGINS = [
  `http://127.0.0.1:${BACKEND_PORT}`,
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
  // ── 1) URL doğrulama (SSRF koruması) ──
  const urlCheck = validateDownloadUrl(url);
  if (!urlCheck.ok) {
    console.error(`[download:pdf] URL reddedildi: ${urlCheck.error} (url=${url})`);
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
    defaultPath: path.join(app.getPath('downloads'), safeName),
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

  // ── 6) HTTP request (redirect takip etmez) ──
  return new Promise((resolve) => {
    const request = net.request({
      method: 'POST',
      url: url,
      redirect: 'error', // Redirect'leri takip etme — SSRF koruması
    });
    request.setHeader('Content-Type', `multipart/form-data; boundary=${boundary}`);
    request.setHeader('Content-Length', String(bodyBuffer.length));

    const chunks = [];
    let totalBytes = 0;
    let statusCode = 0;
    let responseContentType = '';

    request.on('response', (response) => {
      statusCode = response.statusCode;
      responseContentType = (response.headers['content-type'] || '').toString();
      console.log(`[download:pdf] Response: status=${statusCode}, content-type=${responseContentType}`);

      response.on('data', (chunk) => {
        totalBytes += chunk.length;
        // Max boyut kontrolü
        if (totalBytes > MAX_PDF_SIZE) {
          request.abort();
          resolve({ ok: false, error: `PDF boyutu limiti aşıldı (>${MAX_PDF_SIZE / 1024 / 1024}MB).` });
          return;
        }
        chunks.push(chunk);
      });

      response.on('end', () => {
        const buffer = Buffer.concat(chunks);

        // HTTP hata kontrolü — structured JSON error propagation
        if (statusCode !== 200) {
          let errorResult = { ok: false, statusCode, error: `Sunucu hatası (${statusCode})` };
          
          // Backend artık JSON error dönüyor: { error: { code, message, request_id } }
          if (responseContentType.includes('application/json')) {
            try {
              const parsed = JSON.parse(buffer.toString('utf-8'));
              const errObj = parsed.error || parsed;
              errorResult.code = errObj.code || 'unknown';
              errorResult.error = errObj.message || errObj.detail || errorResult.error;
              errorResult.request_id = errObj.request_id || null;
              // 429: Retry-After header'ını da taşı
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

        // Content-Type kontrolü — PDF olmalı
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
    icon: path.join(__dirname, 'icons', 'icon.png'),
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
    mainWindow.loadURL('http://localhost:3000');
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
