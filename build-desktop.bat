@echo off
echo ============================================
echo   Gelka Enerji - Masaustu Uygulama Build
echo ============================================
echo.

:: 0. Versiyon numarasini artir (patch: 1.0.0 -> 1.0.1 -> ...)
:: Boylece her build'in uygulama icinde (footer/Hakkinda) FARKLI bir
:: numarasi olur ve hangi iyilestirmelerin dahil oldugu takip edilebilir.
echo [0/5] Versiyon numarasi artiriliyor...
cd electron
call npm version patch --no-git-tag-version --allow-same-version >nul
for /f "delims=" %%i in ('node -p "require('./package.json').version"') do set APP_VERSION=%%i
cd ..
echo   Yeni versiyon: %APP_VERSION%

:: 1. Build metadata (commit/branch/date/version) - /version endpoint icin
echo [1/5] Build metadata olusturuluyor...
cd backend
for /f "delims=" %%i in ('git rev-parse HEAD') do set GIT_COMMIT=%%i
for /f "delims=" %%i in ('git rev-parse --short HEAD') do set GIT_SHORT=%%i
for /f "delims=" %%i in ('git rev-parse --abbrev-ref HEAD') do set GIT_BRANCH=%%i
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format o"') do set BUILD_DATE=%%i
>build-info.json echo {"commit":"%GIT_COMMIT%","commit_short":"%GIT_SHORT%","branch":"%GIT_BRANCH%","build_date":"%BUILD_DATE%","app_version":"%APP_VERSION%"}
echo   build-info.json: v%APP_VERSION% - %GIT_SHORT% (%GIT_BRANCH%) %BUILD_DATE%
cd ..

:: 2. Frontend build
echo [2/5] Frontend build ediliyor...
cd frontend
call npm run build
if %ERRORLEVEL% neq 0 (
    echo HATA: Frontend build basarisiz!
    pause
    exit /b 1
)
cd ..

:: 3. Backend PyInstaller build
echo [3/5] Backend paketleniyor (PyInstaller)...
cd backend

:: NOT: pip/pyinstaller/python komutlarini DOGRUDAN cagirmiyoruz — bunlar
:: calistiran shell'in PATH durumuna gore SISTEM Python'una (venv disinda)
:: dusebilir ve build'i deterministik olmaktan cikarir. Bunun yerine .venv'in
:: kendi python.exe'sini ACIKCA kullaniyoruz (clean-machine / tekrarlanabilir
:: build gereksinimi).
set VENV_PY=.venv\Scripts\python.exe
if not exist "%VENV_PY%" (
    echo HATA: %VENV_PY% bulunamadi. Once backend\.venv olusturulmali.
    pause
    exit /b 1
)
%VENV_PY% -m pip install pyinstaller >nul 2>&1

:: 3a. Playwright Chromium — PAKET-GORELI konuma kur (kullanicinin
:: %LOCALAPPDATA%\ms-playwright cache'ine DEGIL). PLAYWRIGHT_BROWSERS_PATH=0
:: playwright'in kendi kaynak kodunda ( driver/package/lib/server/registry/
:: index.js ) dogrulanmis resmi davranistir: chromium'u
:: .venv\Lib\site-packages\playwright\driver\package\.local-browsers\ altina
:: indirir. Bu, node.exe'nin zaten arandigi (playwright/_impl/_driver.py:
:: compute_driver_executable, paket-goreli) konumla AYNI agac altinda oldugu
:: icin PyInstaller'in tek bir --add-data ile ikisini birden tasimasini saglar.
:: Yalniz Chromium (Firefox/WebKit YOK).
echo   Playwright Chromium kuruluyor (paket-goreli, PLAYWRIGHT_BROWSERS_PATH=0)...
set PLAYWRIGHT_BROWSERS_PATH=0
%VENV_PY% -m playwright install chromium
if %ERRORLEVEL% neq 0 (
    echo HATA: Playwright chromium kurulumu basarisiz!
    pause
    exit /b 1
)

:: "playwright install chromium" chromium ile birlikte headless-shell ve
:: ffmpeg'i de indirir (playwright'in kendi varsayilan davranisi).
:: DUZELTME (canli testle dogrulandi): headless-shell SILINEMEZ — Playwright
:: 1.49'da p.chromium.launch() (channel verilmeden, varsayilan headless mod)
:: GERCEKTE chromium_headless_shell binary'sini ariyor, normal chrome.exe'yi
:: DEGIL. Bu Firefox/WebKit degil, Chromium ekosisteminin resmi bir parcasi
:: (ayni chromium projesinin headless-only build'i) — "yalniz Chromium"
:: kisitlamasina aykiri degil. Yalniz ffmpeg siliniyor (video/screencast,
:: PDF uretiminde hic kullanilmiyor).
for /d %%D in (".venv\Lib\site-packages\playwright\driver\package\.local-browsers\ffmpeg-*") do rmdir /s /q "%%D"

%VENV_PY% -m PyInstaller --onefile --name gelka-backend ^
    --paths . ^
    --add-data "app;app" ^
    --add-data "prompts;prompts" ^
    --add-data "app/templates;app/templates" ^
    --add-data ".venv\Lib\site-packages\playwright\driver;playwright/driver" ^
    --collect-submodules app ^
    --collect-submodules app.core ^
    --collect-submodules app.guards ^
    --collect-submodules app.invoice ^
    --collect-submodules app.services ^
    --collect-submodules app.pricing ^
    --collect-submodules app.adaptive_control ^
    --collect-submodules app.testing ^
    --collect-submodules fastapi ^
    --collect-submodules starlette ^
    --collect-submodules pydantic ^
    --collect-submodules uvicorn ^
    --collect-submodules sqlalchemy ^
    --collect-submodules playwright ^
    --hidden-import pydantic_settings ^
    --hidden-import dotenv ^
    --hidden-import multipart ^
    --hidden-import python_multipart ^
    --hidden-import python_multipart.multipart ^
    --hidden-import httpx ^
    --hidden-import httpx._transports ^
    --hidden-import httpx._transports.default ^
    --hidden-import openai ^
    --hidden-import PIL ^
    --hidden-import PIL.Image ^
    --hidden-import pypdfium2 ^
    --hidden-import pdfplumber ^
    --hidden-import jinja2 ^
    --hidden-import openpyxl ^
    --hidden-import prometheus_client ^
    --hidden-import email.mime.multipart ^
    --hidden-import email.mime.text ^
    --hidden-import h11 ^
    --hidden-import anyio ^
    --hidden-import anyio._backends ^
    --hidden-import anyio._backends._asyncio ^
    --hidden-import sniffio ^
    --hidden-import idna ^
    --hidden-import certifi ^
    --hidden-import httpcore ^
    run_server.py
if %ERRORLEVEL% neq 0 (
    echo HATA: Backend build basarisiz!
    pause
    exit /b 1
)
cd ..

:: 4. Electron dependencies
echo [4/5] Electron bagimliliklari yukleniyor...
cd electron
call npm install
cd ..

:: 5. Electron build
:: NOT: electron/package.json'daki nsis.artifactName SABIT bir isim
:: (Gelka-Enerji-Setup.exe) belirtir - versiyon numarasi DOSYA ADINDA
:: DEGIL, uygulama icinde (footer) gosterilir. Boylece her build AYNI
:: dosyanin USTUNE yazar, eskisini elle silmeye gerek kalmaz.
echo [5/5] Masaustu uygulamasi olusturuluyor...
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
echo   Build tamamlandi! Versiyon: v%APP_VERSION%
echo   Installer: electron\release\Gelka-Enerji-Setup.exe
echo ============================================
pause
