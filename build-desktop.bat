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
    --add-data "app;app" ^
    --hidden-import uvicorn.logging ^
    --hidden-import uvicorn.protocols.http ^
    --hidden-import uvicorn.protocols.http.auto ^
    --hidden-import uvicorn.protocols.http.h11_impl ^
    --hidden-import uvicorn.protocols.http.httptools_impl ^
    --hidden-import uvicorn.protocols.websockets ^
    --hidden-import uvicorn.protocols.websockets.auto ^
    --hidden-import uvicorn.lifespan ^
    --hidden-import uvicorn.lifespan.on ^
    --hidden-import uvicorn.lifespan.off ^
    --hidden-import sqlalchemy.dialects.sqlite ^
    --hidden-import app.main ^
    --hidden-import app.models ^
    --hidden-import app.database ^
    --hidden-import app.extractor ^
    --hidden-import app.calculator ^
    --hidden-import app.validator ^
    --hidden-import app.pdf_generator ^
    --hidden-import app.epias_client ^
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
