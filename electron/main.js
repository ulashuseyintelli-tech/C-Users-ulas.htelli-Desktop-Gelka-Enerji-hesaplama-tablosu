const { app, BrowserWindow, dialog } = require('electron');
const path = require('path');
const { spawn } = require('child_process');
const http = require('http');

let mainWindow;
let backendProcess;

const BACKEND_PORT = 8000;
const isDev = !app.isPackaged;

// Backend'in hazır olup olmadığını kontrol et
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
  let pythonPath, scriptArgs;

  if (isDev) {
    // Geliştirme modunda: doğrudan python çalıştır
    pythonPath = process.platform === 'win32' ? 'python' : 'python3';
    scriptArgs = ['-m', 'uvicorn', 'app.main:app', '--host', '127.0.0.1', '--port', String(BACKEND_PORT)];
    backendProcess = spawn(pythonPath, scriptArgs, {
      cwd: path.join(__dirname, '..', 'backend'),
      env: { ...process.env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  } else {
    // Paketlenmiş modda: PyInstaller exe veya bundled python
    const backendExe = path.join(process.resourcesPath, 'backend', 'gelka-backend.exe');
    backendProcess = spawn(backendExe, ['--host', '127.0.0.1', '--port', String(BACKEND_PORT)], {
      env: { ...process.env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
  }

  backendProcess.stdout.on('data', (data) => {
    console.log(`[backend] ${data.toString().trim()}`);
  });

  backendProcess.stderr.on('data', (data) => {
    console.error(`[backend] ${data.toString().trim()}`);
  });

  backendProcess.on('error', (err) => {
    console.error('Backend başlatma hatası:', err);
    dialog.showErrorBox('Hata', `Backend başlatılamadı: ${err.message}`);
  });

  backendProcess.on('exit', (code) => {
    console.log(`Backend kapandı (code: ${code})`);
    backendProcess = null;
  });
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
    },
    show: false,
  });

  // Splash / loading ekranı
  mainWindow.once('ready-to-show', () => {
    mainWindow.show();
  });

  if (isDev) {
    // Dev modda Vite dev server'a bağlan
    mainWindow.loadURL('http://localhost:3000');
    mainWindow.webContents.openDevTools();
  } else {
    // Prod modda build edilmiş dosyaları yükle
    mainWindow.loadFile(path.join(process.resourcesPath, 'frontend', 'dist', 'index.html'));
  }

  mainWindow.on('closed', () => {
    mainWindow = null;
  });
}

app.whenReady().then(async () => {
  // Backend'i başlat
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

app.on('window-all-closed', () => {
  stopBackend();
  app.quit();
});

app.on('before-quit', () => {
  stopBackend();
});

app.on('activate', () => {
  if (BrowserWindow.getAllWindows().length === 0) {
    createWindow();
  }
});
