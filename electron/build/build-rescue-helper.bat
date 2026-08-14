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
