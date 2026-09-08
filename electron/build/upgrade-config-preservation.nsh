; ═══════════════════════════════════════════════════════════════════════════
; GO-R93 — UPGRADE CONFIG KORUMASI
;
; KOK NEDEN (R92 izole upgrade/rollback provasinda GERCEKTEN gozlemlendi —
; bkz. R89Z3R9-R92-V6-REHEARSAL-CLOSURE.json): electron/package.json
; extraResources, HER kurulum/upgrade'de "../backend/.env.production"
; dosyasini "resources/backend/.env" UZERINE yazar; bu sablon OPENAI_API_KEY/
; EPIAS_USERNAME/EPIAS_PASSWORD anahtarlarini TANIMLAMAZ (EPIAS_* anahtar
; ADI olarak vardir ama deger BOSTUR). Eski surumun resources/backend/.env'i
; ise uninstallOldVersion tarafindan $INSTDIR ile BIRLIKTE HER ZAMAN silinir —
; bu, $APPDATA'yi koruyan /KEEP_APP_DATA'nin KAPSAMI DISINDADIR (yalniz
; $APPDATA korunur, $INSTDIR HER ZAMAN silinir). Bir kullanici bu
; anahtarlardan birini GERCEKTEN resources/backend/.env'e MANUEL yazmis
; olsaydi (belgelenen machine-local.env deseni yerine), upgrade bu degeri
; SESSIZCE kaybederdi.
;
; ONEMLI DUZELTME (GO-R93 Madde 1'de gercek dosyalar okunarak DOGRULANDI):
; gercek kurulu v1.0.6'nin KENDI .env'i (mtime 2026-08-10, hic degismedi)
; de OPENAI_API_KEY'i ZATEN icermiyor, EPIAS_* zaten BOS — yani bu makine
; icin "upgrade GERCEKTEN bir degeri kaybetti" iddiasi KANITLANAMAZ (kaybedecek
; gercek bir deger yoktu). Asil ISPATLANAN sey MEKANIZMADIR: biri BU
; anahtarlardan birini gelecekte resources/backend/.env'e yazarsa, bir SONRAKI
; upgrade onu sessizce siler — bu yama BUNU KAPATIR (henuz gerceklesmemis
; ama gercek bir riski, gerceklesmeden ONCE kapatir).
;
; COZUM: mevcut, ZATEN calisan machine-local.env mekanizmasini kullan (bkz.
; electron/main.js::loadMachineLocalEnv — outreach_smtp_password ile AYNI
; desen). customInit (uninstallOldVersion'DAN ONCE, installer.nsh'nin kendi
; kanitli hook-sirasi analizine gore) eski .env'de bu anahtarlardan biri
; GERCEKTEN (bos olmayan) VARSA ve machine-local.env'de HENUZ yoksa, DEGERI
; machine-local.env'e TASIR — boylece bir SONRAKI upgrade'den itibaren
; MEVCUT mekanizma onu zaten koruyacaktir ($APPDATA, /KEEP_APP_DATA
; kapsaminda — canonical DB ile AYNI kanit R92'de dogrulandi: DB hash'i
; upgrade+rollback boyunca DEGISMEDEN kaldi).
;
; NELERİ KORUMAZ (KASITLI): DATABASE_URL, STORAGE_DIR, ENV,
; GELKA_PACKAGED_RUNTIME, STORAGE_BACKEND, OPENAI_MODEL, API_KEY_ENABLED,
; WORKER_POLL_INTERVAL, EXTRACTION_*, RATE_LIMIT_ENABLED, TENANT_REQUIRED,
; DEFAULT_TENANT vb. — bunlarin cogu zaten electron/main.js'de
; machineLocalEnv'DEN SONRA literal olarak verilir (PDSMR-R3/R3B/S5-R03B) VE
; surum tarafindan yonetilir; digerleri (OPENAI_MODEL vb.) surumun
; varsayilanini takip etmesi GEREKEN ayarlardir. Buraya EKLENMEMISTIR —
; "eski .env'yi korlemesine geri kopyalama" ilkesi (GO-R93 Madde 1).
; PROTECTED_KEYS listesi KASITLI OLARAK dar tutulur.
;
; FAIL-CLOSED: eski .env'de GERCEK bir deger bulunur AMA tasima denemesi
; sonrasi machine-local.env'de DOGRULANAMAZSA (ör. yazma basarisiz), kurulum
; eski surum SILINMEDEN DURDURULUR — DB-kurtarma (customInit'teki mevcut
; gelka-rescue.exe blogu) ile AYNI ilke.
;
; SIR GUVENLIGI: hicbir deger DetailPrint/log/MessageBox/sonuc dosyasina
; YAZILMAZ — yalniz "kac anahtar tasindi" gibi SAYISAL/yapisal bilgi
; (varsa) loglanabilir, deger ASLA. NSIS degiskenleri yalniz bellekte,
; kurulum surecinin kendi omru boyunca tutulur (Setup.exe disina hicbir
; gecici dosyaya yazilmaz).
;
; KAYIT DEFTERI/ROLLBACK ETKISI: YOK — bu dosya yalniz customInit icinde,
; SHELL_CONTEXT zaten initMultiUser tarafindan ayarlandiktan SONRA calisir;
; mevcut DB-kurtarma/registry/rollback davranisina dokunmaz (bagimsiz,
; katmali blok).
; ═══════════════════════════════════════════════════════════════════════════

; ── Yardimci: $R2'deki satirin sonundaki CR/LF'i temizler (StrFunc.nsh'nin
;    bildirim-sirasi karmasikligindan kacinmak icin elle, NSIS yerlesik
;    StrCpy negatif-uzunluk deseniyle) ────────────────────────────────────
!macro R93_TrimTrailingCRLF Var
  StrCpy $R6 "${Var}" "" -1
  ${if} $R6 == "$\n"
    StrCpy ${Var} "${Var}" -1
  ${endIf}
  StrCpy $R6 "${Var}" "" -1
  ${if} $R6 == "$\r"
    StrCpy ${Var} "${Var}" -1
  ${endIf}
!macroend

; ── Bir .env dosyasinda KeyName= ile baslayan satiri arar; bulursa (bos
;    olmayan degerle) OutVar'a deger, FoundVar'a "1" yazar; yoksa OutVar=""
;    FoundVar="0". PrefixLen = "KEYNAME=" harf sayisi (cagiran verir). ────
!macro R93_ReadKeyFromEnvFile EnvPath KeyName PrefixLen OutVar FoundVar
  StrCpy ${OutVar} ""
  StrCpy ${FoundVar} "0"
  ${if} ${FileExists} "${EnvPath}"
    FileOpen $R7 "${EnvPath}" r
    ${if} $R7 != ""
      ${do}
        ClearErrors
        FileRead $R7 $R8
        ${if} ${Errors}
          ${break}
        ${endif}
        StrCpy $R9 $R8 ${PrefixLen}
        ${if} $R9 == "${KeyName}="
          StrCpy ${OutVar} $R8 "" ${PrefixLen}
          !insertmacro R93_TrimTrailingCRLF ${OutVar}
          ${if} ${OutVar} != ""
            StrCpy ${FoundVar} "1"
          ${endif}
        ${endif}
      ${loop}
      FileClose $R7
    ${endif}
  ${endif}
!macroend

; ── Tek bir korunan anahtari, eksikse eski .env'den machine-local.env'e
;    tasir. Zaten machine-local.env'de (bos olmayan) VARSA HICBIR SEY
;    yapmaz (kullanicinin sonradan elle girdigi deger ASLA ezilmez).
;    MlEnvDir/MlEnvPath ayri parametrelerdir (gercek cagri yerinde
;    "$APPDATA\gelka-enerji" / "$APPDATA\gelka-enerji\machine-local.env" -
;    testte disposable bir fixture dizinine YONLENDIRILEBILIR, GERCEK
;    $APPDATA'ya asla YAZMAZ). ──────────────────────────────────────────
!macro R93_MigrateProtectedKeyIfMissing OldInstallDir KeyName PrefixLen MlEnvDir MlEnvPath
  !insertmacro R93_ReadKeyFromEnvFile "${OldInstallDir}\resources\backend\.env" "${KeyName}" ${PrefixLen} $R1 $R2

  ${if} $R2 == "1"
    ; eski .env'de GERCEK (bos olmayan) bir deger bulundu
    !insertmacro R93_ReadKeyFromEnvFile "${MlEnvPath}" "${KeyName}" ${PrefixLen} $R3 $R4

    ${if} $R4 == "0"
      ; machine-local.env'de HENUZ yok (veya bos) -- tasi
      CreateDirectory "${MlEnvDir}"
      FileOpen $R7 "${MlEnvPath}" a
      ${if} $R7 == ""
        MessageBox MB_OK|MB_ICONSTOP "Yapılandırma koruması başarısız oldu (machine-local.env açılamadı). Kurulum güvenlik nedeniyle durduruldu. Mevcut verileriniz DEĞİŞTİRİLMEDİ."
        Quit
      ${endif}
      FileSeek $R7 0 END
      FileWrite $R7 "${KeyName}=$R1$\r$\n"
      FileClose $R7

      ; DOGRULAMA: sessizce basari VARSAYMA -- GERCEKTEN yazildi mi oku
      !insertmacro R93_ReadKeyFromEnvFile "${MlEnvPath}" "${KeyName}" ${PrefixLen} $R3 $R4
      ${ifNot} $R4 == "1"
        MessageBox MB_OK|MB_ICONSTOP "Yapılandırma koruması doğrulanamadı. Kurulum güvenlik nedeniyle durduruldu. Mevcut verileriniz DEĞİŞTİRİLMEDİ."
        Quit
      ${endif}
    ${endif}
  ${endif}

  StrCpy $R1 ""
  StrCpy $R3 ""
!macroend

; ── customInit'ten cagrilacak tek giris noktasi. OldInstallDir = customInit'in
;    zaten okudugu $R0 (Software\<guid> InstallLocation) -- AYNI kaynak,
;    ikinci bir registry okuma YOK. MlEnvDir/MlEnvPath GERCEK cagri
;    yerinde "$APPDATA\gelka-enerji" / "...\machine-local.env" olarak
;    verilir (testte fixture'a yonlendirilebilir). ─────────────────────
!macro R93_ProtectUserSecretsBeforeUpgrade OldInstallDir MlEnvDir MlEnvPath
  ${if} "${OldInstallDir}" != ""
  ${andIf} ${FileExists} "${OldInstallDir}\resources\backend\.env"
    !insertmacro R93_MigrateProtectedKeyIfMissing "${OldInstallDir}" "OPENAI_API_KEY" 15 "${MlEnvDir}" "${MlEnvPath}"
    !insertmacro R93_MigrateProtectedKeyIfMissing "${OldInstallDir}" "EPIAS_USERNAME" 15 "${MlEnvDir}" "${MlEnvPath}"
    !insertmacro R93_MigrateProtectedKeyIfMissing "${OldInstallDir}" "EPIAS_PASSWORD" 15 "${MlEnvDir}" "${MlEnvPath}"
  ${endif}
!macroend
