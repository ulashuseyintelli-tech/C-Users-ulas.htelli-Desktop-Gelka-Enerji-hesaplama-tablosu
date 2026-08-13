; PDSMR-R2 — installer PRE-CLEANUP veritabani kurtarma hook'u.
;
; HOOK SIRASI KANITI (app-builder-lib 25.1.8, node_modules/app-builder-lib/
; templates/nsis/, salt-okunur incelendi — bu turda GERCEK installer
; DERLENMEDI, yalniz kaynak sablonlar okundu):
;
;   installer.nsi  Function .onInit
;     -> check64BitAndSetRegView
;     -> ALLOW_ONLY_ONE_INSTALLER_INSTANCE
;     -> initMultiUser
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
; calisir. customInstall ise COK GEC — o noktada eski surum ZATEN
; silinmis olabilir. Bu yuzden kurtarma customInit'e baglanir.
;
; BU TURDA YAPILMAYAN: rescue.exe'nin PyInstaller ile GERCEKTEN
; derlenmesi ve package.json'daki `nsis.include` alanina bu dosyanin
; baglanmasi. Bunlar GERCEK installer URETIMI (electron-builder --win)
; gerektirir ve PDSMR-R2'de "Real installer execution: NOT AUTHORIZED"
; kapsamindadir. Bu dosya, hook'un DOGRU YERDE ve DOGRU SIRADA
; tanimlanacagini kanitlamak icin yazilmistir; gelecekte ayri bir
; packaging gorevinde (1) `pyinstaller app/legacy_adoption/rescue.py
; --onefile --name gelka-rescue`, (2) bu dosyanin package.json
; `build.nsis.include`'a eklenmesi ile tamamlanir.

!macro customInit
  ; .onInit icinde calisir — INSTDIR henuz eski kurulumun dizini
  ; DEGILDIR (yeni kurulumun hedef dizinidir) ve installApplicationFiles
  ; HENUZ CALISMAMISTIR: bu noktada resources/ dizini SILINMEMISTIR.
  ;
  ; Asagidaki akis TASARIM SOZLESMESIDIR (gercek build'de etkinlestirilir):
  ;
  ;   InitPluginsDir
  ;   File /oname=$PLUGINSDIR\gelka-rescue.exe "${BUILD_RESOURCES_DIR}\gelka-rescue.exe"
  ;
  ;   ; eski kurulumun InstallLocation'i (varsa) registry'den okunur —
  ;   ; uninstallOldVersion'in KULLANDIGI AYNI kaynaktir (bkz.
  ;   ; installUtil.nsh Function uninstallOldVersion), boylece rescue
  ;   ; VE eski-surum-silme AYNI dizini hedefler.
  ;   ReadRegStr $R0 SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" InstallLocation
  ;   ${if} $R0 != ""
  ;   ${andIf} ${FileExists} "$R0\resources\backend\gelka_enerji.db"
  ;     ; canonical hedef: $APPDATA\gelka-enerji\database\gelka_enerji.db —
  ;     ; Electron app.getPath('userData') ile AYNI formul (bkz.
  ;     ; app/legacy_adoption/pathsafety.py::resolve_canonical_db_path,
  ;     ; electron/dbRouting.js::resolveCanonicalDbPath — uc dilde de
  ;     ; TEK sabit deger).
  ;     ExecWait '"$PLUGINSDIR\gelka-rescue.exe" \
  ;       --legacy "$R0\resources\backend\gelka_enerji.db" \
  ;       --canonical "$APPDATA\gelka-enerji\database\gelka_enerji.db" \
  ;       --backups-dir "$APPDATA\gelka-enerji\database\backups" \
  ;       --version-label "${VERSION}"' $0
  ;
  ;     ${if} $0 != 0
  ;       ; RescueRefused: kurtarma basarisiz oldu. Kurulumu DURDUR —
  ;       ; "uyar ve devam et" YASAKTIR (owner karari, PDSMR-R2).
  ;       MessageBox MB_OK|MB_ICONSTOP \
  ;         "Veritabani tasima islemi basarisiz oldu. Kurulum guvenlik \
  ;          nedeniyle durduruldu. Mevcut verileriniz DEGISTIRILMEDI."
  ;       Quit
  ;     ${endIf}
  ;   ${endIf}
  ;
  ; Bu turda satirlar YORUM olarak birakilmistir: rescue.exe henuz
  ; derlenmedi (bkz. yukaridaki not). Etkinlestirme, ayri bir packaging
  ; PR'inda yapilmalidir.
!macroend
