@echo off
echo ============================================
echo   Gelka Enerji - Masaustu Uygulama Build
echo ============================================
echo.

:: 0. Build metadata (commit/branch/date) - /version endpoint icin
echo [0/4] Build metadata olusturuluyor...
cd backend
for /f "delims=" %%i in ('git rev-parse HEAD') do set GIT_COMMIT=%%i
for /f "delims=" %%i in ('git rev-parse --short HEAD') do set GIT_SHORT=%%i
for /f "delims=" %%i in ('git rev-parse --abbrev-ref HEAD') do set GIT_BRANCH=%%i
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format o"') do set BUILD_DATE=%%i
>build-info.json echo {"commit":"%GIT_COMMIT%","commit_short":"%GIT_SHORT%","branch":"%GIT_BRANCH%","build_date":"%BUILD_DATE%","app_version":"1.0.0"}
echo   build-info.json: %GIT_SHORT% (%GIT_BRANCH%) %BUILD_DATE%
cd ..

:: 1. Frontend build
echo [1/4] Frontend build ediliyor...
cd frontend
call npm run build
if %ERRORLEVEL% neq 0 (
    echo HATA: Frontend build basarisiz!
    pause
    exit /b 1
)
cd ..

:: 2. Backend PyInstaller build
:: ONEMLI: Sistem Python'una guvenilmez (surumu/bagimliliklari degisebilir,
:: fastapi/uvicorn/reportlab kurulu olmayabilir - bu durum daha once exe'yi
:: "No module named uvicorn" ile cokerten 13MB'lik bos bir stub'a donusturdu).
:: Bu yuzden backend\.venv icinde izole, bagimliliklari bilinen bir Python kullanilir.
:: Hidden-import listesi burada TEKRAR EDILMEZ - tek dogru kaynak gelka-backend.spec.
echo [2/4] Backend paketleniyor (PyInstaller, .venv icinden)...
cd backend
if not exist ".venv\Scripts\python.exe" (
    echo   backend\.venv bulunamadi, olusturuluyor...
    py -3.13 -m venv .venv
    if %ERRORLEVEL% neq 0 (
        echo HATA: venv olusturulamadi! Python 3.13 kurulu mu kontrol edin ^(py -0p^).
        pause
        exit /b 1
    )
)
echo   Bagimliliklar kontrol ediliyor/kuruluyor...
.venv\Scripts\python.exe -m pip install -q -r requirements.txt pyinstaller
if %ERRORLEVEL% neq 0 (
    echo HATA: Bagimlilik kurulumu basarisiz!
    pause
    exit /b 1
)
.venv\Scripts\python.exe -m PyInstaller --noconfirm --clean gelka-backend.spec
if %ERRORLEVEL% neq 0 (
    echo HATA: Backend build basarisiz!
    pause
    exit /b 1
)
cd ..

:: 3. Electron dependencies
echo [3/4] Electron bagimliliklari yukleniyor...
cd electron
call npm install
cd ..

:: 4. Electron build
echo [4/4] Masaustu uygulamasi olusturuluyor...
echo winCodeSign cache hazirlaniyor (symlink sorunu icin)...
if exist "%LOCALAPPDATA%\electron-builder\Cache\winCodeSign" rmdir /s /q "%LOCALAPPDATA%\electron-builder\Cache\winCodeSign"
cd electron
set CSC_IDENTITY_AUTO_DISCOVERY=false
set WIN_CSC_LINK=
set CSC_LINK=
call npx electron-builder --win --config.forceCodeSigning=false
if %ERRORLEVEL% neq 0 (
    echo HATA: Electron build basarisiz!
    pause
    exit /b 1
)
cd ..

echo.
echo ============================================
echo   Build tamamlandi!
echo   Installer: electron/release/
echo ============================================
pause
