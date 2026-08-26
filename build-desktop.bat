@echo off
:: PDSMR-R3B — script'in KENDI konumuna gore calisir, cagrildigi CWD'den
:: BAGIMSIZ (bazi cagiran kabuklarda/araclarda CWD senkronizasyonu guvenilmez
:: olabiliyor - GERCEK denemeyle yasandi). %~dp0 = bu .bat dosyasinin KENDI
:: sürücü+dizini, HER ZAMAN dogru.
cd /d "%~dp0"
echo ============================================
echo   Gelka Enerji - Masaustu Uygulama Build
echo ============================================
echo.

:: PDSMR-S5-RC-PREP — RELEASE PREFLIGHT (fail-closed): worktree ONCE, HERHANGI
:: bir adimdan (versiyon belirleme dahil) ONCE TEMIZ olmali - boylece
:: committed package.json'daki versiyon, "bu build'in GERCEKTEN temsil ettigi
:: versiyon" olarak GUVENILIR olur (owner karari: "clean master'dan tek
:: immutable RC uret" gereksinimi, HER calistirmada kosulsuz versiyon artiran
:: eski davranisla CELISIYORDU).
set GIT_DIRTY=0
for /f "delims=" %%i in ('git status --porcelain 2^>nul') do set GIT_DIRTY=1
if "%GIT_DIRTY%"=="1" (
    echo HATA: worktree TEMIZ DEGIL - release build kirli calisma agacindan calistirilamaz.
    git status --short
    pause
    exit /b 1
)

:: 0. Versiyon DOGRULANIR (ARTIK KOSULSUZ ARTIRILMAZ).
:: ONCEKI davranis: "npm version patch" HER build'de CALISIYORDU - bu,
:: committed package.json'daki degerin RC icin guvenilir versiyon-kaynagi
:: olmasini ENGELLIYORDU (owner: "1.0.12 diyorsa 1.0.12'yi koru, diagnostic
:: rebuild'ler yuzunden ARTIRMA"). Varsayilan davranis ARTIK: committed
:: package.json'daki versiyon OLDUGU GIBI kullanilir. Versiyon artirma ayri,
:: acik bir opt-in'dir: "set BUMP_VERSION=1 ^&^& build-desktop.bat" (dev
:: iterasyonu icin - normal release build'in yan etkisi DEGILDIR).
echo [0/5] Versiyon dogrulaniyor...
cd electron
if "%BUMP_VERSION%"=="1" (
    echo   BUMP_VERSION=1 - versiyon ACIKCA artiriliyor ^(opt-in^)...
    call npm version patch --no-git-tag-version --allow-same-version >nul
)
for /f "delims=" %%i in ('node -p "require('./package.json').version"') do set APP_VERSION=%%i
for /f "delims=" %%i in ('node -p "require('./package-lock.json').version"') do set LOCK_VERSION=%%i
cd ..
echo   Versiyon: %APP_VERSION%

echo %APP_VERSION%| findstr /r /c:"^[0-9][0-9]*\.[0-9][0-9]*\.[0-9][0-9]*$" >nul
if errorlevel 1 (
    echo HATA: versiyon gecerli semver ^(X.Y.Z^) formatinda degil: %APP_VERSION%
    pause
    exit /b 1
)
if not "%APP_VERSION%"=="%LOCK_VERSION%" (
    echo HATA: package.json versiyonu ^(%APP_VERSION%^) package-lock.json ile ^(%LOCK_VERSION%^) UYUSMUYOR.
    pause
    exit /b 1
)

:: Onceki AYNI-adli installer artifact'i varsa (bu run'un ciktisi ile eski
:: kalintinin karismamasi icin acikca) temizlenir.
if exist "electron\release\Gelka-Enerji-Setup.exe" (
    echo   Onceki installer artifact'i temizleniyor...
    del /q "electron\release\Gelka-Enerji-Setup.exe"
)

:: PDSMR-S5-RC-PREP — build-info.json'a gomulecek "dirty" bayragi + BUILD
:: SIRASINDA kaynak dosyalarda beklenmeyen degisiklik olup olmadigini
:: SONDA dogrulamak icin BASLANGIC anlik goruntusu. (Versiyon belirleme
:: ADIMINDAN SONRA aliniyor - BUMP_VERSION=1 kullanildiysa package.json/lock
:: DEGISIKLIGI dirty=true olarak DOGRU sekilde yansitilir; sonraki build
:: asamalari HICBIR ek tracked-dosya degisikligi YAPMAMALIDIR.)
set BUILD_DIRTY=false
for /f "delims=" %%i in ('git status --porcelain 2^>nul') do set BUILD_DIRTY=true
git status --porcelain > "%TEMP%\gelka_rc_status_before.txt" 2>nul

:: 1. Build metadata (commit/branch/date/version) - /version endpoint icin
echo [1/5] Build metadata olusturuluyor...
cd backend
for /f "delims=" %%i in ('git rev-parse HEAD') do set GIT_COMMIT=%%i
for /f "delims=" %%i in ('git rev-parse --short HEAD') do set GIT_SHORT=%%i
for /f "delims=" %%i in ('git rev-parse --abbrev-ref HEAD') do set GIT_BRANCH=%%i
for /f "delims=" %%i in ('powershell -NoProfile -Command "Get-Date -Format o"') do set BUILD_DATE=%%i
>build-info.json echo {"commit":"%GIT_COMMIT%","commit_short":"%GIT_SHORT%","branch":"%GIT_BRANCH%","build_date":"%BUILD_DATE%","app_version":"%APP_VERSION%","dirty":%BUILD_DIRTY%}
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

:: PDSMR-R3B STEP 1 — PyInstaller'i "%VENV_PY% -m PyInstaller" YERINE
:: console-script .exe'si UZERINDEN cagiriyoruz. NEDEN: "python -m X" CWD'yi
:: (backend\) sys.path[0]'a KOSULSUZ ekler (Python'un KENDI -m davranisi,
:: --paths . bayragindan TAMAMEN BAGIMSIZ) - GERCEK derlemeyle KANITLANDI
:: (PDSMR-R3B): --paths . kaldirilsa DAHI "-m PyInstaller" ile derlenen exe
:: HALA backend/alembic/ (proje migration klasoru) ile GERCEK alembic
:: paketini KARISTIRIYORDU (EXIT 52). pyinstaller.exe (bu script'in ait
:: oldugu backend\.venv\Scripts\ altinda) bu CWD-otomatik-ekleme davranisina
:: SAHIP DEGIL - AYRI, izole testle KANITLANDI.
set PYINSTALLER_EXE=.venv\Scripts\pyinstaller.exe
if not exist "%PYINSTALLER_EXE%" (
    echo HATA: %PYINSTALLER_EXE% bulunamadi.
    pause
    exit /b 1
)

:: PDSMR-R3B STEP 2 — DETERMINISTIK TEMIZ DERLEME: PyInstaller'in build\
:: dizini ONCEKI analizi ONBELLEKLER (.toc dosyalari) - bayat bytecode/analiz
:: SESSIZCE yeniden kullanilabilir (PDSMR-R3A'da GERCEK olarak yasandi: eski
:: __pycache__/build/ kalintisi YUZUNDEN, KAYNAK KODU duzeltilmis olsa bile,
:: derlenen exe'ye ESKI alembic_runner.py gomulmustu). Bu YUZDEN HER
:: derlemeden once YALNIZ bu build'e AIT, ureteceğimiz artifact'lari ACIKCA
:: sileriz - baska HICBIR seye (ozellikle backend/dist/gelka-backend.exe
:: DISINDAKI baska dist/ klasorlerine, ör. frontend/dist, electron/dist)
:: DOKUNMAYIZ.
::
:: DUZELTME (GERCEK derlemeyle YAKALANDI): asagidaki __pycache__ silme
:: dongusu ONCEDEN baslangic dizini VERMEDEN "for /d /r %%D in (__pycache__)"
:: seklindeydi - CWD bu noktada backend\ oldugundan, bu KOSULSUZ olarak
:: backend\.venv\Lib\site-packages\**\__pycache__ (ANA repo ile PAYLASILAN,
:: junction uzerinden erisilen venv) icine de SIZIYORDU: (a) Step 2'nin
:: "YALNIZ bu build'e AIT/owned artifacts" kapsaminin DISINDA - ucuncu parti
:: paket pycache'i "bizim KAYNAK KODUMUZUN bayat analizi" riskiyle ILGISIZ;
:: (b) GERCEK build ciktisinda gorulen "Sistem belirtilen yolu bulamiyor"
:: hata gurultusunun KOK NEDENI (ic ice __pycache__ silinirken ust dizin
:: ONCE kaldiriliyor, dongu sonraki hedefi artik MEVCUT OLMAYAN bir yolda
:: ariyor); (c) HER derlemede TUM venv'in bytecode onbellegini bosa
:: siliyordu (yavaslik). Duzeltme: YALNIZ bizim git-tracked kaynak
:: dizinlerimiz (app, alembic, scripts, tests) taranir - .venv'e HIC girilmez.
echo   Onceki build/dist/spec/__pycache__ temizleniyor (deterministik derleme)...
if exist build rmdir /s /q build
if exist dist rmdir /s /q dist
if exist gelka-backend.spec del /q gelka-backend.spec
for %%S in (app alembic scripts tests) do (
    for /d /r "%%S" %%D in (__pycache__) do @if exist "%%D" rmdir /s /q "%%D"
)
if exist __pycache__ rmdir /s /q __pycache__

:: PDSMR-R3B STEP 1 — DERLEME-ONCESI (pre-build) DOGRULAMA: PyInstaller'i
:: (~2-3 dakika suren analiz/derleme) HIC CALISTIRMADAN, `import alembic`'in
:: bu derleme ortaminda YUKLU ucuncu parti pakete cozuldugunu (backend/alembic/
:: proje klasoru TARAFINDAN GOLGELENMEDIGINI) VE `app` paketinin HALA
:: bulunabilir oldugunu KANITLAR - bkz. scripts/assert_alembic_identity.py.
echo   Derleme-oncesi dogrulama: alembic kimligi + app bulunabilirligi...
%VENV_PY% scripts\assert_alembic_identity.py
if %ERRORLEVEL% neq 0 (
    echo HATA: derleme-oncesi dogrulama BASARISIZ - PyInstaller CALISTIRILMADI.
    pause
    exit /b 1
)

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

:: PDSMR-R3B STEP 2 — build komutu + bagimlilik surumleri KAYDEDILIR (asagida,
:: build BASARILI olduktan SONRA build-info.json'a SHA256 ile birlikte
:: eklenir - bkz. asagidaki "Build metadata" adimi).
::
:: PDSMR-R3B STEP 3/7 — --hidden-import html.parser: bu satirlar `^` ile
:: DEVAM EDEN TEK bir komut olusturur - yorum satiri BURAYA, komutun
:: ICINE konursa cmd.exe onu satir-birlestirmeden SONRA PyInstaller'a
:: GERCEK bir ARGUMAN olarak GECIRIR (GERCEK derlemeyle KANITLANDI, bkz.
:: git gecmisi - bu yuzden asagida DEGIL, BURADA, zincirin DISINDA durur).
:: html.parser (stdlib; app/prospecting/discovery.py DuckDuckGo HTML
:: ayristirmasi icin kullanir) PyInstaller'in STATIK analizinde gozden
:: kacmisti - GERCEK paketlenmis exe'yle (Step 7 pozitif matris) YAKALANDI:
:: schema kapisi 351d314819d5'e BASARIYLA ulastiktan HEMEN SONRA app.main
:: import zincirinde ModuleNotFoundError.
::
:: S5-R03A — --collect-submodules reportlab: paketli PDF motoru (PRIMARY=
:: ReportLab, app/pdf_generator.py). S5-R03 RC1 HARD STOP kok nedeni: bu
:: zincir reportlab'i TOPLAMIYORDU (RC exe PYZ'sinde 0 modul; kurulu
:: v1.0.6'da 97 modul) — packaged'da REPORTLAB_AVAILABLE=False kaliyor ve
:: teklif PDF uretimi oluyordu. MINIMUM KUME KARARI: collect-submodules
:: (yalniz moduller) YETERLI — v1.0.6 paritesi resource=0 (CArchive'de
:: reportlab data girdisi yok), kod reportlab/fonts dizinini KULLANMIYOR
:: (TTF sistem fontundan: C:/Windows/Fonts/arial.ttf, yoksa base-14
:: Helvetica metrikleri _fontdata_* PYTHON modullerinden gelir), C
:: extension yok (rl_accel saf-Python fallback). --collect-all (data
:: dahil) BILEREK secilmedi. Dogrulama: scripts/packaged_pdf_smoke.py
:: PYZ envanterini + gercek uretimi mekanik kontrol eder; bu flag
:: kaldirilirsa smoke FAIL olur.
%PYINSTALLER_EXE% --onefile --name gelka-backend ^
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
    --collect-submodules PIL ^
    --collect-submodules reportlab ^
    --hidden-import pydantic_settings ^
    --hidden-import dotenv ^
    --hidden-import multipart ^
    --hidden-import python_multipart ^
    --hidden-import python_multipart.multipart ^
    --hidden-import httpx ^
    --hidden-import httpx._transports ^
    --hidden-import httpx._transports.default ^
    --hidden-import openai ^
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
    --hidden-import logging.config ^
    --hidden-import html.parser ^
    --collect-all alembic ^
    run_server.py
if %ERRORLEVEL% neq 0 (
    echo HATA: Backend build basarisiz!
    pause
    exit /b 1
)

:: PDSMR-R3B STEP 2 — artifact SHA256 + derleme komutu + bagimlilik surumleri
:: build-info.json'a EKLENIR (mevcut alanlar KORUNUR - app/main.py bunlari
:: `.get(key, default)` ile okur, BILINMEYEN/YENI anahtarlar zararsizdir).
:: Karmasik cok-satirli PowerShell mantigi AYRI bir .ps1 dosyasinda (batch
:: icine gomulu, kirilgan tirnak/devam sozdizimi yerine).
for /f "delims=" %%H in ('powershell -NoProfile -Command "(Get-FileHash 'dist\gelka-backend.exe' -Algorithm SHA256).Hash"') do set BACKEND_EXE_SHA256=%%H
echo   gelka-backend.exe SHA256: %BACKEND_EXE_SHA256%
powershell -NoProfile -ExecutionPolicy Bypass -File scripts\update_build_info.ps1 -Sha256 "%BACKEND_EXE_SHA256%" -VenvPython "%VENV_PY%"
if %ERRORLEVEL% neq 0 (
    echo HATA: build-info.json guncellenemedi!
    pause
    exit /b 1
)
cd ..

:: 4. Electron dependencies
echo [4/5] Electron bagimliliklari yukleniyor...
cd electron
call npm install
cd ..

:: PDSMR-R3B — gelka-rescue.exe (PDSMR-R2I kurtarma yardimcisi) HER
:: derlemede TAZE uretilir (backend\dist_rescue\ -> electron\build\ altina
:: kopyalanir). Bu binary BILEREK git'e commit EDILMEZ (bkz. .gitignore
:: satiri + owner karari, PDSMR-R2I) ve build-desktop.bat ONCEDEN bu
:: adimi HICBIR YERDEN cagirmiyordu (GERCEK derlemeyle YAKALANAN bosluk —
:: bkz. build-desktop-output.log: "no files found" / "Error in macro
:: customInit"). installer.nsh'nin customInit makrosu bu dosyayi installer
:: .exe'nin ICINE gomer; eksikse NSIS paketleme KESIN basarisiz olur.
:: build-rescue-helper.bat (PDSMR-R2I'de zaten dogrulanmis) DEGISTIRILMEDEN,
:: oldugu gibi, commit SHA'siyla cagrilir.
echo   gelka-rescue.exe olusturuluyor (electron\build\build-rescue-helper.bat, PDSMR-R2I)...
call electron\build\build-rescue-helper.bat %GIT_COMMIT%
if %ERRORLEVEL% neq 0 (
    echo HATA: gelka-rescue.exe build basarisiz - NSIS customInit bu dosyayi bulamaz!
    pause
    exit /b 1
)

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

:: PDSMR-S5-RC-PREP — BUILD-ORTASI KAYNAK DEGISIKLIGI TESPITI (fail-closed):
:: build BASARIYLA tamamlanmis GORUNSE bile, eger tracked kaynak dosyalari
:: build SIRASINDA (baslangictaki versiyon-belirleme SONRASI ile BURASI
:: arasinda) beklenmedik sekilde degistiyse, bu artifact GUVENILIR bir
:: RELEASE ADAYI DEGILDIR - butun build ciktilari (dist/build/release/
:: node_modules) zaten .gitignore'da oldugundan, bu karsilastirma YALNIZ
:: GERCEK kaynak-kodu mutasyonlarini yakalar.
if exist "%TEMP%\gelka_rc_status_before.txt" (
    git status --porcelain > "%TEMP%\gelka_rc_status_after.txt" 2>nul
    fc /b "%TEMP%\gelka_rc_status_before.txt" "%TEMP%\gelka_rc_status_after.txt" >nul
    if errorlevel 1 (
        echo HATA: build SIRASINDA kaynak dosyalarinda BEKLENMEYEN degisiklik tespit edildi!
        echo   Versiyon belirleme SONRASI durum:
        type "%TEMP%\gelka_rc_status_before.txt"
        echo   Simdiki durum:
        type "%TEMP%\gelka_rc_status_after.txt"
        del /q "%TEMP%\gelka_rc_status_before.txt" "%TEMP%\gelka_rc_status_after.txt" 2>nul
        pause
        exit /b 1
    )
    del /q "%TEMP%\gelka_rc_status_before.txt" "%TEMP%\gelka_rc_status_after.txt" 2>nul
)

echo.
echo ============================================
echo   Build tamamlandi! Versiyon: v%APP_VERSION%
echo   Installer: electron\release\Gelka-Enerji-Setup.exe
echo   Dirty: %BUILD_DIRTY%
echo ============================================
pause
