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
pip install pyinstaller >nul 2>&1
pyinstaller --onefile --name gelka-backend ^
    --paths . ^
    --add-data "app;app" ^
    --add-data "prompts;prompts" ^
    --add-data "app/templates;app/templates" ^
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
