@echo off
REM PDSMR-R2I - gelka-rescue.exe deterministik build komutu.
REM
REM Uretilen exe:
REM   - sistem Python'una bagimli DEGILDIR (--onefile, gomulu yorumlayici)
REM   - ag erisimi GEREKTIRMEZ
REM   - konsol etkilesimi ICERMEZ (argparse ile calisir, stdin okumaz)
REM   - --version ile surum/build provenance yazdirir
REM
REM Cikti backend\dist_rescue\gelka-rescue.exe olarak uretilir, SONRA
REM electron\build\gelka-rescue.exe'ye KOPYALANIR — installer.nsh bu
REM exe'yi derleme ZAMANINDA ${BUILD_RESOURCES_DIR}\gelka-rescue.exe
REM olarak NSIS paketinin ICINE gomer (bkz. installer.nsh yorumlari;
REM ${APP_BUILD_DIR} DEGIL — bu projede 7z sikistirma kullanildigi icin
REM o degisken derleme zamaninda TANIMSIZ kalir, GERCEK derlemeyle
REM KANITLANDI). Her iki dizin de .gitignore'daki Python "dist/" kurali
REM ve elle eklenen "!electron/build/gelka-rescue.exe" ISTISNASI HARIC
REM tutulur — binary COMMIT EDILMEZ, yalniz bu betik commit edilir.

setlocal

set BUILD_SHA=%1
if "%BUILD_SHA%"=="" set BUILD_SHA=unknown

REM PYINSTALLER_EXE: worktree'lerde .venv paylasilmaz (git-tracked degil).
REM Varsayilan olarak bu betigin kendi backend\.venv'ini arar; farkli bir
REM yorumlayici/venv kullanmak icin ORTAM DEGISKENI ile GECERSIZ KILINABILIR
REM (boslukla argumani cmd.exe/PowerShell arasi tirnaklama sorunlarindan
REM  kacinmak icin argument yerine env var tercih edildi — provada
REM  KANITLANDI, bkz. PDSMR-R2I ADIM 1):
REM   set "PYINSTALLER_EXE=C:\...\.venv\Scripts\pyinstaller.exe"
REM   build-rescue-helper.bat <build_sha>
if "%PYINSTALLER_EXE%"=="" set PYINSTALLER_EXE=.venv\Scripts\pyinstaller.exe

REM R66 -- PYINSTALLER REPRODUCIBILITY AUTHORITY (SOURCE_DATE_EPOCH + PYTHONHASHSEED).
REM R65'te STATIK olarak kanitlanan iki bagimsiz nondeterminism kaynagi:
REM   1) PE TimeDateStamp -- PyInstaller.building.api.EXE.assemble() ->
REM      os.environ.get('SOURCE_DATE_EPOCH', time.time()) -- set edilmezse wall-clock.
REM   2) base_library.zip entry-yazma sirasi -- PYTHONHASHSEED sabitlenmezse
REM      Python'un set/dict hash-randomization'i modulegraph traversal sirasini
REM      calistirmadan calistirmaya degistirir.
REM Ikisi de PyInstaller/CPython'un KENDI standart env-var mekanizmasidir --
REM KAYNAK PATCH GEREKMEZ. Deger BUILD_SHA (zaten yukarida cozumlendi) ISTIKAMETINDEN
REM turetilir -- R61/R62 NSIS mtime normalization'in KULLANDIGI AYNI commit-epoch
REM otoritesi (yeni bir sabit ICAT EDILMEDI). Wall-clock KULLANILMAZ; git basarisiz/
REM bos/decimal-olmayan cikti verirse FAIL-CLOSED (sessizce time.time()'a DUSULMEZ).
REM setlocal/endlocal ZATEN bu betigin cevresini sarmaladigi icin asagidaki set
REM komutlari CAGIRANIN ortamina SIZMAZ.
if "%PYTHONHASHSEED%"=="" (
  set PYTHONHASHSEED=0
) else if not "%PYTHONHASHSEED%"=="0" (
  echo [gelka-rescue build] FAIL-CLOSED: ambient PYTHONHASHSEED=%PYTHONHASHSEED% beklenen 0 ile CELISIYOR
  exit /b 1
)

set R66_SOURCE_DATE_EPOCH_COMPUTED=
for /f "usebackq delims=" %%e in (`git show -s --format^=%%ct %BUILD_SHA% 2^>nul`) do set R66_SOURCE_DATE_EPOCH_COMPUTED=%%e
if "%R66_SOURCE_DATE_EPOCH_COMPUTED%"=="" (
  echo [gelka-rescue build] FAIL-CLOSED: git show basarisiz/bos cikti, SOURCE_DATE_EPOCH turetilemedi ^(BUILD_SHA=%BUILD_SHA%^)
  exit /b 1
)
echo %R66_SOURCE_DATE_EPOCH_COMPUTED%| findstr /r "^[0-9][0-9]*$" >nul
if errorlevel 1 (
  echo [gelka-rescue build] FAIL-CLOSED: git show decimal-olmayan cikti dondurdu: %R66_SOURCE_DATE_EPOCH_COMPUTED%
  exit /b 1
)
if "%SOURCE_DATE_EPOCH%"=="" (
  set SOURCE_DATE_EPOCH=%R66_SOURCE_DATE_EPOCH_COMPUTED%
) else if not "%SOURCE_DATE_EPOCH%"=="%R66_SOURCE_DATE_EPOCH_COMPUTED%" (
  echo [gelka-rescue build] FAIL-CLOSED: ambient SOURCE_DATE_EPOCH=%SOURCE_DATE_EPOCH% beklenen %R66_SOURCE_DATE_EPOCH_COMPUTED% ile CELISIYOR
  exit /b 1
)
set R66_SOURCE_DATE_EPOCH_COMPUTED=

cd /d "%~dp0..\..\backend"

REM Build SHA'yi CALISMA-ZAMANI env var'i olarak degil, PyInstaller
REM runtime-hook'u araciligiyla EXE'nin ICINE GOMERIZ — env var yaklasimi
REM (bir onceki denemede provada YAKALANDI) exe CALISIRKEN farkli bir
REM process oldugu icin isbe yaramaz; runtime-hook ise PKG'ye gomulup
REM HER baslangicta calisir.
> build_rescue_provenance_hook.py (
  echo import os
  echo os.environ.setdefault^("PDSMR_RESCUE_BUILD_SHA", "%BUILD_SHA%"^)
)

"%PYINSTALLER_EXE%" ^
  --onefile ^
  --name gelka-rescue ^
  --console ^
  --clean ^
  --noconfirm ^
  --distpath dist_rescue ^
  --workpath build_rescue ^
  --specpath build_rescue ^
  --paths . ^
  --runtime-hook build_rescue_provenance_hook.py ^
  rescue_entry.py

del build_rescue_provenance_hook.py 2>nul

copy /y dist_rescue\gelka-rescue.exe "%~dp0gelka-rescue.exe" >nul
if errorlevel 1 (
  echo [gelka-rescue build] KOPYALAMA BASARISIZ
  exit /b 1
)
echo [gelka-rescue build] electron\build\gelka-rescue.exe olustu

if errorlevel 1 (
  echo [gelka-rescue build] BASARISIZ
  exit /b 1
)

echo [gelka-rescue build] TAMAMLANDI: backend\dist_rescue\gelka-rescue.exe
endlocal
