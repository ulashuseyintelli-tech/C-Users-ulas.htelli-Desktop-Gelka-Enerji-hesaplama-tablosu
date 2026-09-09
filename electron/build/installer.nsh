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
;
; GO-R93: upgrade sirasinda kullanici sirlarinin (OPENAI_API_KEY, EPIAS_*)
; sessizce kaybolmasini onleyen bagimsiz blok - bkz.
; upgrade-config-preservation.nsh basindaki detayli aciklama.
!include "upgrade-config-preservation.nsh"

!macro customInit
  ; .onInit icinde calisir - INSTDIR henuz eski kurulumun dizini
  ; DEGILDIR (yeni kurulumun hedef dizinidir) ve installApplicationFiles
  ; HENUZ CALISMAMISTIR: bu noktada resources/ dizini SILINMEMISTIR.
  InitPluginsDir
  File /oname=$PLUGINSDIR\gelka-rescue.exe "${BUILD_RESOURCES_DIR}\gelka-rescue.exe"

  ; ── $APPDATA, SetShellVarContext'e GORE DEGISIR (initMultiUser BURADAN
  ; ONCE calisir): "sadece benim icin" -> current -> Roaming AppData
  ; ($APPDATA=C:\Users\<kullanici>\AppData\Roaming); "tum kullanicilar
  ; icin" -> all -> $APPDATA ARTIK C:\ProgramData'YA DONUSUR. Bu installer
  ; oneClick:false oldugu icin (assistedInstaller.nsh + multiUser.nsh,
  ; app-builder-lib 25.1.8, dogrudan okunarak DOGRULANDI) "tum kullanicilar
  ; icin" secenegi HEM sihirbazda bir radyo-dugmesi (PAGE_INSTALL_MODE)
  ; HEM DE komut satirinda /allusers ile HER ZAMAN erisilebilir - nsis.
  ; perMachine:false BUNU KAPATMAZ (tetikleyici !oneClick'tir, perMachine
  ; DEGIL; NsisTarget.js: "if (!oneClick || perMachine) INSTALL_MODE_
  ; PER_ALL_USERS_REQUIRED"). allowElevation:false de yetersiz - zaten
  ; admin/UAC-gecmis bir kullaniciyi VE /allusers komut-satiri bayragini
  ; (initMultiUser'da, elevation-gate'in yasadigi sihirbaz sayfasindan
  ; BAGIMSIZ, kosulsuz okunur) hic kapsamaz.
  ;
  ; Electron'un app.getPath('userData')'si ISE HER ZAMAN, kurulum
  ; modundan BAGIMSIZ, mevcut oturum acmis kullanicinin Roaming
  ; AppData'sidir - Electron'da "tum kullanicilar icin" kavrami YOKTUR
  ; (main.js::loadMachineLocalEnv, dbRouting.js::resolveCanonicalDbPath).
  ; Kurulum "tum kullanicilar icin" ile yapilirsa, asagidaki DB-kurtarma
  ; VE GO-R93 blogu ham $APPDATA kullansaydi C:\ProgramData\gelka-enerji\...
  ; hedefler, calisan Electron ise SESSIZCE C:\Users\<kullanici>\AppData\
  ; Roaming\gelka-enerji\...'i okurdu - veri sessizce YANLIS yere giderdi.
  ;
  ; DUZELTME: $installMode (initMultiUser tarafindan zaten "all" veya
  ; "CurrentUser" olarak ayarlanmis GERCEK NSIS degiskeni, ayni karsilastirma
  ; multiUserUi.nsh'de de kullanilir) ile baglami SADECE bu iki satir icin
  ; gecici olarak "current"e alip HEMEN geri donduruyoruz - baska hicbir
  ; yan etkisi yok, $DESKTOP/$STARTMENU/kisayol/kurulum-hedefi gibi
  ; sonraki adimlar ETKILENMEDEN kullanicinin GERCEKTEN sectigi modda kalir.
  ;
  ; $9 SECIMI KASITLIDIR: hem bu makro hem upgrade-config-preservation.nsh
  ; SADECE $R0-$R9'u ic gecici (scratch) olarak kullanir (R93_ReadKeyFrom
  ; EnvFile $R9'a kadar YAZAR) - bir $RN verilirse makro kendi icinde bu
  ; degeri EZER. $0-$9 (R'siz) bu dosyada sadece $0 (ExecWait sonucu)
  ; kullanir; $9 HICBIR yerde dokunulmaz, bu yuzden customInit'in SONUNA
  ; kadar guvenle tasinir.
  ${if} $installMode == "all"
    SetShellVarContext current
    StrCpy $9 "$APPDATA"
    SetShellVarContext all
  ${else}
    StrCpy $9 "$APPDATA"
  ${endif}

  ; Eski kurulumun InstallLocation'i (varsa) registry'den okunur -
  ; uninstallOldVersion'in KULLANDIGI AYNI kaynaktir (bkz.
  ; installUtil.nsh Function uninstallOldVersion), boylece rescue
  ; VE eski-surum-silme AYNI dizini hedefler. Bu, per-machine VEYA
  ; per-user kurulum oldugundan bagimsiz calisir (SHELL_CONTEXT
  ; initMultiUser tarafindan zaten dogru context'e ayarlanmistir).
  ReadRegStr $R0 SHELL_CONTEXT "${INSTALL_REGISTRY_KEY}" InstallLocation

  ${if} $R0 != ""
  ${andIf} ${FileExists} "$R0\resources\backend\gelka_enerji.db"
    ; canonical hedef: $9\gelka-enerji\database\gelka_enerji.db (yukarida
    ; cozulen, HER ZAMAN mevcut kullanicinin Roaming AppData'si) -
    ; Electron app.getPath('userData') ile AYNI formul (bkz.
    ; app/legacy_adoption/pathsafety.py::resolve_canonical_db_path,
    ; electron/dbRouting.js::resolveCanonicalDbPath - uc dilde de
    ; TEK sabit deger, "gelka-enerji" package.json 'name' alanindan).
    ExecWait '"$PLUGINSDIR\gelka-rescue.exe" --legacy "$R0\resources\backend\gelka_enerji.db" --canonical "$9\gelka-enerji\database\gelka_enerji.db" --backups-dir "$9\gelka-enerji\database\backups" --version-label "${VERSION}"' $0

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
    ${orIfNot} ${FileExists} "$9\gelka-enerji\database\gelka_enerji.db"
      ; RescueRefused / baslatilamadi / beklenmedik durum: kurtarma
      ; basarili OLDUGU KANITLANAMADI. Kurulumu DURDUR - "uyar ve devam
      ; et" YASAKTIR (owner karari, PDSMR-R2).
      ; Mesaj METNI kasitli olarak GENELDIR: gercek dosya yolu/kullanici
      ; adi kullaniciya GORUNMEZ (rescue.exe kendi stderr'inde de
      ; sanitize eder, bkz. sanitize_for_log()).
      ; GO-R93/R94: /SD IDOK -- BUNSUZ, SilentInstall/gercek /S kurulumunda
      ; bu MessageBox GERCEK bir dialog acmaya CALISIR ve kimse tiklamadigi
      ; icin SONSUZA KADAR HANG EDER (GO-R93 Madde 1'de standalone makensis
      ; testiyle DOGRUDAN gozlemlendi -- upgrade-config-preservation.nsh'deki
      ; AYNI kusur, AYNI sekilde duzeltildi). Silent DEGILSE davranis AYNI
      ; kalir (kullanici yine gorur/tiklar); /SD SADECE silent modda
      ; otomatik-cevap saglar.
      MessageBox MB_OK|MB_ICONSTOP "Veritabani tasima islemi basarisiz oldu (kod: $0). Kurulum guvenlik nedeniyle durduruldu. Mevcut verileriniz DEGISTIRILMEDI." /SD IDOK
      Quit
    ${endIf}
  ${endIf}

  ; GO-R93: DB-kurtarmadan BAGIMSIZ, ayri katman - AYNI $R0'i (eski
  ; InstallLocation) yeniden kullanir, ikinci bir registry okuma YOK.
  ; Fresh install ($R0=="") veya eski .env yoksa NO-OP. Hedef,
  ; electron/main.js::loadMachineLocalEnv'in okudugu AYNI, GERCEK yol -
  ; $9 yukarida cozulen, HER ZAMAN mevcut kullanicinin Roaming AppData'si.
  !insertmacro R93_ProtectUserSecretsBeforeUpgrade $R0 "$9\gelka-enerji" "$9\gelka-enerji\machine-local.env"
!macroend
