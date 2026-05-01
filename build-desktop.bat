@echo off
echo ============================================
echo   Gelka Enerji - Masaustu Uygulama Build
echo ============================================
echo.

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
echo [2/4] Backend paketleniyor (PyInstaller)...
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
