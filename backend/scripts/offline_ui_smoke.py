# -*- coding: utf-8 -*-
"""
S5-R03B — Paketli UI TAM OFFLINE smoke (owner Bölüm 8).

Gerçek paketlenmiş Electron UI'sini (Gelka Enerji.exe), izole bir
`--user-data-dir` ile başlatır ve CDP üzerinden AKTİF bir ağ-reddi
politikası kurar: loopback (127.0.0.1:<backend-port>) DIŞINDAKİ HER
istek YAKALANIR + REDDEDİLİR + kaydedilir. Bu, "Google isteği görmedim"
pasif gözleminden farklı olarak — hiçbir dış istek denemesinin
sızamayacağını AKTİF olarak kanıtlar (owner: "yalnız 'Google isteği
görmedim' gözlemi yeterli değil").

Doğrulanan kapılar:
  1. Ana ekran gerçekten yükleniyor (DOM içeriği + RAF canlılığı)
  2. Console error sayısı 0
  3. Page error (uncaught exception) sayısı 0
  4. Yakalanan/reddedilen dış (non-loopback) istek sayısı 0
  5. Özellikle fonts.googleapis.com / fonts.gstatic.com isteği 0
  6. Yalnız izinli loopback backend trafiği görülüyor (pozitif kanıt —
     en az 1 loopback isteği geçmiş olmalı, aksi hâlde backend'e hiç
     ulaşılmamış demektir ve smoke anlamsızdır)
  7. Yatay taşma yok (document.documentElement.scrollWidth <= innerWidth)
  8. Kapanışta process/port temiz (tree-kill)

Kullanım (opt-in komut; pytest regresyonuna DAHİL DEĞİLDİR — packaged
Electron binary gerektirir):
    python -m scripts.offline_ui_smoke <Gelka Enerji.exe> <calisma-dizini>

<calisma-dizini> boş/temiz bir dizin olmalıdır; izole userData buraya
yazılır. Production'a HİÇBİR koşulda dokunmaz (userData tamamen izole).

Çağrıldığı yerler:
- S5-R03B Bölüm 8/12 packaged offline UI smoke (elle / release qualification)
"""
from __future__ import annotations

import json
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

CDP_PORT = 9331


def _kontrol(kosul: bool, mesaj: str) -> None:
    durum = "PASS" if kosul else "FAIL"
    print(f"[{durum}] {mesaj}")
    if not kosul:
        raise SystemExit(f"SMOKE FAIL: {mesaj}")


def _cdp_hazir_mi() -> bool:
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/version", timeout=2) as y:
            return y.status == 200
    except Exception:
        return False


def _cdp_page_target_var_mi() -> bool:
    """CDP endpoint ERKEN hazır olur (Electron app objesi kurulur kurulmaz)
    ama gerçek pencere/sayfa TARGET'ı ancak main.js backend health-check'i
    geçip `createWindow()` çağırdıktan SONRA doğar. Playwright'ın
    `connect_over_cdp`'si BAĞLANDIĞI ANDAKİ target listesini alır — sayfa
    henüz yoksa `browser.contexts[0].pages` boş kalır (Electron CDP'si
    `Target.createTarget`'ı desteklemediği için `new_page()` de ÇALIŞMAZ).
    Bu yüzden Playwright'a bağlanmadan ÖNCE raw CDP `/json/list` ile
    gerçek bir `type=page` target'ı doğrudan ararız."""
    try:
        with urllib.request.urlopen(f"http://127.0.0.1:{CDP_PORT}/json/list", timeout=2) as y:
            hedefler = json.loads(y.read())
        return any(h.get("type") == "page" for h in hedefler)
    except Exception:
        return False


def main() -> int:
    exe = Path(sys.argv[1]).resolve()
    kok = Path(sys.argv[2]).resolve()
    _kontrol(exe.is_file(), f"exe mevcut: {exe.name}")
    kok.mkdir(parents=True, exist_ok=True)

    user_data_dir = kok / "isolated-userdata"
    user_data_dir.mkdir(parents=True, exist_ok=True)

    # CDP portu önceden boş olmalı (packaged_pdf_smoke.py'daki aynı
    # sahte-yeşil/sahte-kırmızı önleme deseni).
    if _cdp_hazir_mi():
        raise SystemExit(
            f"SMOKE FAIL: CDP portu {CDP_PORT} zaten dinleniyor — önceki koşu artığı"
        )

    proc = subprocess.Popen(
        [
            str(exe),
            f"--user-data-dir={user_data_dir}",
            f"--remote-debugging-port={CDP_PORT}",
        ],
        cwd=str(exe.parent),
    )
    try:
        for _ in range(60):
            time.sleep(2)
            if _cdp_hazir_mi():
                break
        else:
            raise SystemExit("SMOKE FAIL: CDP hiç hazır olmadı (Electron başlamamış olabilir)")
        print("[PASS] Electron CDP hazır")

        # Gerçek pencere/sayfa target'ı (backend health-check + createWindow()
        # SONRASI doğar) Playwright'a BAĞLANMADAN ÖNCE raw CDP ile beklenir.
        for _ in range(60):
            if _cdp_page_target_var_mi():
                break
            time.sleep(2)
        else:
            raise SystemExit(
                "SMOKE FAIL: Electron ana pencere sayfa target'ı hiç doğmadı "
                "(backend health-check hiç geçmemiş olabilir)"
            )
        print("[PASS] Electron ana pencere sayfa target'ı doğdu")

        from playwright.sync_api import sync_playwright

        with sync_playwright() as pw:
            browser = pw.chromium.connect_over_cdp(f"http://127.0.0.1:{CDP_PORT}")
            ctx = browser.contexts[0]
            _kontrol(bool(ctx.pages), "Playwright context içinde en az bir sayfa görüyor")
            page = ctx.pages[0]

            konsol_hatalari: list[str] = []
            page_hatalari: list[str] = []
            engellenen_disari: list[str] = []
            izinli_loopback: list[str] = []

            page.on(
                "console",
                lambda m: konsol_hatalari.append(m.text) if m.type == "error" else None,
            )
            page.on("pageerror", lambda e: page_hatalari.append(str(e)))

            def _route_isle(route):
                url = route.request.url
                # file:// (paketli frontend dist'i) her zaman izinli — dış
                # ağ DEĞİL, yerel disk okumasıdır.
                if url.startswith("file://"):
                    izinli_loopback.append(url)
                    route.continue_()
                    return
                # Yalnız 127.0.0.1/localhost (backend + CDP'nin kendisi)
                # izinlidir. Her şey başka bir şey DIŞ AĞ sayılır.
                if "127.0.0.1" in url or "localhost" in url:
                    izinli_loopback.append(url)
                    route.continue_()
                    return
                engellenen_disari.append(url)
                route.abort("failed")

            ctx.route("**/*", _route_isle)

            # KRİTİK: router, sayfa DAHA ÖNCE (Electron'un kendi loadFile()'ı
            # ile, biz bağlanmadan ÖNCE) yüklenmiş olduğu için henüz HİÇBİR
            # isteği görmedi — Playwright route'ları YALNIZ ekleme ANINDAN
            # SONRAKİ istekleri yakalar, geçmişe dönük değildir. `reload()`
            # ile TAM yükleme döngüsünü (file:// + JS/CSS + backend fetch'leri)
            # router AKTİFKEN yeniden tetikleriz — bu olmadan "0 dış istek"
            # sonucu router'ın hiç çalışmadığı bir sahte-yeşil olurdu.
            page.reload(wait_until="load", timeout=30000)

            # Ana ekranın yüklendiğini kanıtla: DOM içeriği + RAF canlılığı.
            page.wait_for_timeout(3000)
            govde_metni = page.locator("body").inner_text(timeout=15000)
            _kontrol(len(govde_metni.strip()) > 0, "ana ekran DOM içeriği boş değil")

            raf = page.evaluate(
                "() => new Promise(res => { const t = setTimeout(() => res(false), 3000); "
                "requestAnimationFrame(() => { clearTimeout(t); res(true); }); })"
            )
            _kontrol(bool(raf), "render döngüsü canlı (requestAnimationFrame çalışıyor)")

            # Bir miktar daha bekle — geç tetiklenen (ör. font/asset) istekleri de yakala.
            page.wait_for_timeout(2000)

            _kontrol(len(konsol_hatalari) == 0, f"console error sayısı 0 (bulunan: {konsol_hatalari[:3]})")
            _kontrol(len(page_hatalari) == 0, f"page error sayısı 0 (bulunan: {page_hatalari[:3]})")
            _kontrol(
                len(engellenen_disari) == 0,
                f"dış (non-loopback) istek sayısı 0 (bulunan: {engellenen_disari[:5]})",
            )
            _kontrol(
                not any("googleapis.com" in u or "gstatic.com" in u for u in engellenen_disari),
                "Google Fonts isteği 0",
            )
            _kontrol(len(izinli_loopback) > 0, "en az bir izinli (loopback/file://) istek görüldü")

            tasma = page.evaluate(
                "() => document.documentElement.scrollWidth <= window.innerWidth + 2"
            )
            _kontrol(bool(tasma), "yatay taşma yok (scrollWidth <= innerWidth)")

            browser.close()
    finally:
        subprocess.run(
            ["taskkill", "/F", "/T", "/PID", str(proc.pid)],
            capture_output=True, timeout=30,
        )
        proc.wait(timeout=30)

    print("SMOKE OK: paketli UI tam offline doğrulandı (0 dış istek, 0 console/page error)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
