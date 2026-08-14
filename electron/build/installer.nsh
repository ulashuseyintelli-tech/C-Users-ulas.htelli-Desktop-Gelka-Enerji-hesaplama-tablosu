; PDSMR-R2 / R2I - installer PRE-CLEANUP veritabani kurtarma hook'u.
;
; HOOK SIRASI KANITI (app-builder-lib 25.1.8, node_modules/app-builder-lib/
; templates/nsis/, salt-okunur incelendi; R2I'de GERCEK NSIS derlemesiyle
; de teyit edildi - bkz. tests/test_legacy_adoption_installer_hooks.py):
;
;   installer.nsi  Function .onInit
;     -> check64BitAndSetRegView
;     -> ALLOW_ONLY_ONE_INSTALLER_INSTANCE
;     -> initMultiUser                    (SHELL_CONTEXT BURADA set edilir)
;     -> !ifmacrodef customInit  !insertmacro customInit   <-- BURASI
;     -> addLicenseFiles
;   FunctionEnd
;
;   installSection.nsh  Section "install"
;     -> !insertmacro uninstallOldVersion SHELL_CONTEXT      (ESKI SURUMU SILER)
;     -> SetOutPath $INSTDIR
;     -> !insertmacro installApplicationFiles                (YENI DOSYALARI YAZAR)
;     -> !ifmacrodef customInstall  !insertmacro customInstall
;
; SONUC: customInit, uninstallOldVersion'DAN (ve dolayisiyla
; resources/backend/gelka_enerji.db'nin silinebilecegi ilk andan) ONCE
; calisir. customInstall ise COK GEC - o noktada eski surum ZATEN
; silinmis olabilir. Bu yuzden kurtarma customInit'e baglanir.
;
; GOMME STRATEJISI: gelka-rescue.exe NSIS'in kendisine (installer .exe
; dosyasinin ICINE) GOMULUR.
;
; ONEMLI DUZELTME (R2I gercek derleme ile YAKALANDI): ilk tasarim
; ${APP_BUILD_DIR} kullaniyordu - bu, electron-builder'in PAKETLENMIS
; app-image dizinini (win-unpacked benzeri) gosterir AMA yalniz NSIS'in
; KENDI (built-in) sikistiricisi VE tek mimari ile build edildiginde
; TANIMLANIR. Bu projede varsayilan 7z sikistirma kullanildigi icin
; (COMPRESSION_METHOD=7z, uygulama dosyalari runtime'da acilan bir .7z
; arsivine paketlenir) ${APP_BUILD_DIR} DERLEME ZAMANINDA BOS/TANIMSIZ
; kalir ve `File` komutu "no files found" ile BASARISIZ olur - bu, GERCEK
; bir `electron-builder --win nsis` derlemesiyle KANITLANDI.
;
; DOGRU degisken: ${BUILD_RESOURCES_DIR} - electron-builder'in KAYNAK
; build-resources dizinini (bu depoda electron/build/) gosterir ve HER
; ZAMAN, sikistirma/mimari secimine BAKMAKSIZIN derleme zamaninda
; mevcuttur (log kaniti: "Command line defined: BUILD_RESOURCES_DIR=...").
; gelka-rescue.exe bu yuzden extraResources ile PAKETLENMEZ (runtime
; kopyasi resources/ ile birlikte silinebilirdi), dogrudan electron/build/
; ALTINA (kaynak/build config dizini, git'e commit EDILMEZ, .gitignore'da)
; PyInstaller build script'i tarafindan YERLESTIRILIR.
;
; Bu, resources/ dizini runtime'da SILINSE BILE customInit'in rescue.exe'ye
; erisimini ETKILEMEZ - cunku dosya derleme aninda PLUGINSDIR'a degil,
; NSIS PAKETININ KENDISINE gomulmustur; customInit calisirken PLUGINSDIR'a
; CIKARILIR.
;
; ${APP_GUID} / ${INSTALL_REGISTRY_KEY} zaten derleme zamaninda
; multiUser.nsh tarafindan tanimlanir (bu betikten ONCE include edilir).

!macro customInit
  ; .onInit icinde calisir - INSTDIR henuz eski kurulumun dizini
  ; DEGILDIR (yeni kurulumun hedef dizinidir) ve installApplicationFiles
  ; HENUZ CALISMAMISTIR: bu noktada resources/ dizini SILINMEMISTIR.
  InitPluginsDir
  File /oname=$PLUGINSDIR\gelka-rescue.exe "${BUILD_RESOURCES_DIR}\gelka-rescue.exe"

  ; Eski kurulumun InstallLocation'i (varsa) registry'den okunur -
  ; uninstallOldVersion'in KULLANDIGI AYNI kaynaktir (bkz.
  ; installUtil.nsh Function uninstallOldVersion), boylece rescue
  ; VE eski-surum-silme AYNI dizini hedefler. Bu, per-machine VEYA
  ; per-user kurulum oldugundan bagimsiz calisir (SHELL_CONTEXT
  ; initMultiUser tarafindan zaten dogru context'e ayarlanmistir).
  ReadRegStr $R0 SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" InstallLocation

  ${if} $R0 != ""
  ${andIf} ${FileExists} "$R0\resources\backend\gelka_enerji.db"
    ; canonical hedef: $APPDATA\gelka-enerji\database\gelka_enerji.db -
    ; Electron app.getPath('userData') ile AYNI formul (bkz.
    ; app/legacy_adoption/pathsafety.py::resolve_canonical_db_path,
    ; electron/dbRouting.js::resolveCanonicalDbPath - uc dilde de
    ; TEK sabit deger, "gelka-enerji" package.json 'name' alanindan).
    ; $APPDATA NSIS built-in yol sabitidir; Electron'un
    ; app.getPath('appData') ile AYNI OS per-user Roaming AppData
    ; kokunu verir - kullanici adi/dil/TR karakter/silent modundan
    ; ETKILENMEZ (formulde bu girdiler hic yer almaz).
    ExecWait '"$PLUGINSDIR\gelka-rescue.exe" --legacy "$R0\resources\backend\gelka_enerji.db" --canonical "$APPDATA\gelka-enerji\database\gelka_enerji.db" --backups-dir "$APPDATA\gelka-enerji\database\backups" --version-label "${VERSION}"' $0

    ; PDSMR-R2I DUZELTMESI (gercek derleme+kurulumla YAKALANDI): $0 TEK
    ; BASINA GUVENILMEZ. gelka-rescue.exe gecerli bir PE/EXE DEGILSE
    ; (bozulmus/degistirilmis kopya) CreateProcess BASARISIZ olur, ANCAK
    ; bu ortamda ExecWait boyle bir durumda $0'i "error" DEGIL, "0" olarak
    ; birakiyor - yani "baslatilamadi" ile "calisti ve 0 dondu" $0 TEK
    ; BASINA AYIRT EDILEMIYOR (gercek tampered-helper testiyle olculdu,
    ; NSIS dokumantasyonunun "error" iddiasindan FARKLI GOZLEMLENDI).
    ;
    ; BU YUZDEN ek, POZITIF bir kanit ARANIR: gercek basarili bir rescue
    ; (RESCUED VEYA zaten-mevcut NOOP) canonical DB dosyasini HER ZAMAN
    ; var eder (rescue.py::perform_rescue - PASS/HARD_STOP disinda cikis
    ; yoktur). Bu dosya YOKSA, $0 ne derse desin BASARISIZ SAY.
    ${if} $0 != 0
    ${orIfNot} ${FileExists} "$APPDATA\gelka-enerji\database\gelka_enerji.db"
      ; RescueRefused / baslatilamadi / beklenmedik durum: kurtarma
      ; basarili OLDUGU KANITLANAMADI. Kurulumu DURDUR - "uyar ve devam
      ; et" YASAKTIR (owner karari, PDSMR-R2).
      ; Mesaj METNI kasitli olarak GENELDIR: gercek dosya yolu/kullanici
      ; adi kullaniciya GORUNMEZ (rescue.exe kendi stderr'inde de
      ; sanitize eder, bkz. sanitize_for_log()).
      MessageBox MB_OK|MB_ICONSTOP "Veritabani tasima islemi basarisiz oldu (kod: $0). Kurulum guvenlik nedeniyle durduruldu. Mevcut verileriniz DEGISTIRILMEDI."
      Quit
    ${endIf}
  ${endIf}
!macroend
