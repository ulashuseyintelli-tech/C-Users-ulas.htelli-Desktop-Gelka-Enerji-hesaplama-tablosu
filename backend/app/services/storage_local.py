"""
Local Filesystem Storage Backend.

For development and simple deployments.
"""
import hashlib
import os
import logging
import stat as stat_modul
import tempfile
import time
from pathlib import Path
from typing import Optional

from app.core.config import settings
from app.services.storage_backend import StorageBackend

logger = logging.getLogger(__name__)

# S5-R01: Windows'ta `os.replace`, hedef dosya başka bir okuyucu tarafından
# açıkken PermissionError verebilir (Python `open()` FILE_SHARE_DELETE
# vermez). Bu geçici bir handle durumudur; sınırlı sayıda yeniden denenir.
# Süre dolarsa hata YUKARI FIRLATILIR — sessizce başarısız olunmaz.
#
# Bütçe ~2 sn: yerel bir PDF indirmesi (birkaç yüz KB) milisaniyeler sürer;
# 2 sn yavaş disk / antivirüs taraması gibi gerçekçi gecikmeleri kapsar.
# SÜREKLİ okunan bir hedef (ör. bitmeyen stream) bu bütçeyi tüketebilir ve
# yayın REDDEDİLİR. Bu bilinçli bir tercihtir: eski davranış hedefin üzerine
# yazıp okuyucuya YIRTIK içerik gösterirdi; artık hedef ya eski sağlam
# içeriği korur ya da yeni içeriğe atomik geçer — ara durum asla oluşmaz.
# Bkz. tests/test_s5_r01_storage_atomic.py::test_surekli_okunan_hedef_*
_PUBLISH_RETRY_COUNT = 40
_PUBLISH_RETRY_DELAY_SECONDS = 0.05


class LocalStorage(StorageBackend):
    """Local filesystem storage.

    S5-R03B — dual-root sözleşmesi:
    - `base_dir`: durable/upgrade-safe kalıcı kök (packaged'da Electron
      `userData/storage`'ı STORAGE_DIR env'i ile geçirir). TÜM yeni yazımlar
      buraya gider; `put_bytes` artık bu köke göre RELATIVE bir anahtar
      döndürür (mutlak yol DEĞİL — bkz. put_bytes docstring).
    - `legacy_base_dir` (opsiyonel): eski/kurulum-dizini-göreli kök
      (`resources/backend/storage`). Yalnız OKUMA için tanınır — buraya asla
      YAZILMAZ, buradan asla silme YAPILMAZ. Belirtilmezse (dev/test
      varsayılanı) legacy okuma yolu devre dışıdır; tek kök `base_dir`'dir.
    """

    def __init__(self, base_dir: str | None = None, legacy_base_dir: str | None = None):
        ham_base_dir = base_dir or settings.storage_dir
        self._reparse_point_ise_fail_closed(ham_base_dir)
        self.base_dir = Path(ham_base_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        # mkdir SONRASI da denetlenir: dizin YENİ oluşturulduysa öncesinde
        # bir reparse point OLAMAZDI, ama zaten var olan bir dizin ilk
        # kontrolden sonra (TOCTOU) değiştirilmiş olabilir — ikinci, ucuz
        # bir denetim ekstra güvenlik marjı sağlar.
        self._reparse_point_ise_fail_closed(str(self.base_dir))

        self.legacy_base_dir: Optional[Path] = (
            Path(legacy_base_dir).resolve() if legacy_base_dir else None
        )

        logger.info(
            f"LocalStorage initialized: base_dir={self.base_dir} "
            f"legacy_base_dir={self.legacy_base_dir}"
        )

    @staticmethod
    def _reparse_point_ise_fail_closed(ham_yol: str) -> None:
        """
        Durable storage kökünün KENDİSİ bir symlink/junction/reparse point
        ise FAIL CLOSED (S5-R03B Bölüm 6). Aksi hâlde kurulum dizinindeki
        (veya başka bir yerdeki) bir reparse point, PDF'lerin GÖRÜNMEZ
        şekilde beklenmeyen bir hedefe yazılmasına yol açabilir.

        Yalnız dizin ZATEN VARSA denetlenir (henüz yoksa reparse point
        olması imkânsızdır — `mkdir` normal bir dizin oluşturur).

        `os.stat(..., follow_symlinks=False).st_file_attributes` ile
        `FILE_ATTRIBUTE_REPARSE_POINT` biti kontrol edilir — bu, Windows'ta
        HEM symlink HEM junction/mount-point'i (ikisi de NTFS reparse
        point'idir) doğru tespit ettiği ampirik olarak doğrulanmış tek
        stdlib mekanizmasıdır (`os.path.islink` junction'ları güvenilir
        yakalamaz).

        Raises:
            ValueError: kök bir reparse point ise.
        """
        p = Path(ham_yol)
        if not p.exists():
            return
        try:
            st = os.stat(str(p), follow_symlinks=False)
        except OSError:
            return  # erişilemiyorsa asıl mkdir/erişim adımı zaten patlayacak
        ozellikler = getattr(st, "st_file_attributes", None)
        if ozellikler is None:
            return  # Windows dışı platform — reparse point kavramı yok
        if ozellikler & stat_modul.FILE_ATTRIBUTE_REPARSE_POINT:
            raise ValueError(
                f"Durable storage kökü bir symlink/junction/reparse point "
                f"olamaz (fail-closed): {ham_yol}"
            )

    def put_bytes(self, key: str, data: bytes, content_type: str) -> str:
        """
        Bytes'i yerel dosya sistemine ATOMİK olarak yazar.

        S5-R01 öncesi doğrudan hedef dosyaya yazılıyordu; yarıda kesilen veya
        eşzamanlı iki yazım yırtık (torn) dosya bırakabiliyor, indirme yarım
        dosya okuyabiliyordu. Artık yazım hedefle AYNI DİZİNDE geçici bir
        dosyaya yapılır, fsync ile diske indirilir, sonra `os.replace` ile
        atomik olarak yayımlanır. Hedef ya eski sağlam içeriği ya da yeni tam
        içeriği gösterir; ARA DURUM YOKTUR. Eski sağlam hedef, yeni dosya
        tamamen hazır olmadan silinmez.

        Dönüş sözleşmesi S5-R03B'DE DEĞİŞTİ: artık `key`'in KENDİSİ (relative,
        portable, mantıksal anahtar) döner — mutlak fiziksel yol DEĞİL. Önceki
        sözleşme (mutlak yol) kurulum dizinini `pdf_ref` içine gömüyordu; bu,
        upgrade'de silinen bir dizine kalıcı referans anlamına geliyordu.
        Fiziksel yol yalnız `resolve_local_path()` ile, durable `base_dir`'e
        göre, gerektiğinde çözülür.

        Çağrıldığı yerler:
        - pdf_generator.generate_and_store_offer_pdf() → POST /offers/{id}/generate-pdf
        - contracts.service.upload_reference_document() → POST /contracts/documents/upload
        - contracts.service.finalize_contract_pdf_and_commit() → POST /contracts/{id}/finalize
        - services.pdf_artifact_store.PdfArtifactStore.put() → teklif PDF artifact deposu
        - main.py fatura orijinal/page1 yüklemeleri (POST /extract, /process)
        - LocalStorage.migrate_legacy_artifact() → legacy dosyayı durable köke taşır
        """
        path = self.base_dir / key
        path.parent.mkdir(parents=True, exist_ok=True)

        # Geçici dosya HEDEFLE AYNI DİZİNDE: `os.replace` yalnız aynı
        # filesystem üzerinde atomiktir. `mkstemp` benzersiz adı yarış
        # koşulusuz üretir (O_CREAT|O_EXCL).
        fd, temp_path = tempfile.mkstemp(
            dir=str(path.parent), prefix=f".{path.name}.", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "wb") as f:
                f.write(data)
                f.flush()
                os.fsync(f.fileno())  # bytes gerçekten diskte
            # Handle burada kapandı → Windows'ta replace için dosya serbest.
            self._atomic_publish(temp_path, str(path))
        except BaseException:
            # Geçici dosya HER hata yolunda temizlenir (residual = 0).
            self._temizle(temp_path)
            raise

        logger.debug(f"Stored {len(data)} bytes to {path} (atomic)")
        return key  # S5-R03B: relative/portable anahtar — mutlak yol DEĞİL

    @staticmethod
    def _temizle(temp_path: str) -> None:
        """Geçici dosyayı sessizce siler; silinemezse yalnız uyarır."""
        try:
            if os.path.exists(temp_path):
                os.remove(temp_path)
        except OSError:
            logger.warning(f"Geçici dosya temizlenemedi: {temp_path}")

    @classmethod
    def _atomic_publish(cls, temp_path: str, final_path: str) -> None:
        """
        Geçici dosyayı `os.replace` ile atomik olarak yayımlar.

        `os.replace` hedef var olsa bile tek adımda değiştirir; eski içerik
        yeni dosya tamamen hazır olana kadar korunur. Windows handle
        çakışması için sınırlı yeniden deneme uygulanır (bkz. modül başı).

        Çağrıldığı yerler:
        - LocalStorage.put_bytes() → tüm yerel storage yazımları
        """
        son_hata: Optional[OSError] = None
        for _ in range(_PUBLISH_RETRY_COUNT):
            try:
                os.replace(temp_path, final_path)
                return
            except PermissionError as e:  # Windows: hedef okunuyor olabilir
                son_hata = e
                time.sleep(_PUBLISH_RETRY_DELAY_SECONDS)
        raise OSError(
            f"Atomik yayın başarısız (hedef meşgul): {final_path}"
        ) from son_hata

    def get_bytes(self, ref: str) -> bytes:
        """Read bytes from local filesystem."""
        path = self.resolve_local_path(ref)
        with open(path, "rb") as f:
            return f.read()

    def exists(self, ref: str) -> bool:
        """Check if file exists."""
        try:
            path = self.resolve_local_path(ref)
            return os.path.exists(path)
        except ValueError:
            return False

    def delete(self, ref: str) -> bool:
        """Delete file."""
        try:
            path = self.resolve_local_path(ref)
            if os.path.exists(path):
                os.remove(path)
                return True
            return False
        except Exception as e:
            logger.error(f"Delete failed for {ref}: {e}")
            return False

    def resolve_local_path(self, ref: str) -> str:
        """
        Resolve and validate local path.

        Security: Prevents path traversal attacks by ensuring
        the resolved path is within storage_dir (or the recognized
        legacy_base_dir — read-through compatibility, bkz. aşağı).

        S5-R01: `Path.resolve()` Windows'ta symlink/junction/reparse
        point'leri de çözer; containment kontrolü çözülmüş yol üzerinde
        yapıldığı için symlink escape de reddedilir. Karşılaştırma
        `relative_to` ile yapılır — string prefix karşılaştırması DEĞİL
        (aksi hâlde `storage_dir_evil` gibi kardeş dizinler kabul edilirdi).

        S5-R03B — dual-root + CWD-bağımsızlık düzeltmesi:
        - `ref` RELATIVE ise (yeni/portable anahtar, `put_bytes`'ın güncel
          dönüşü): HER ZAMAN `self.base_dir`'e göre çözülür — ÖNCEKİ hata
          `Path(ref).resolve()` çağrısının relative bir ref'i sürecin O ANKİ
          ÇALIŞMA DİZİNİNE (CWD) göre çözmesiydi; packaged modda CWD =
          kurulum dizini (`resources/backend`) olduğundan bu, durable köke
          taşınmanın TÜM amacını sessizce boşa çıkarırdı.
        - `ref` ABSOLUTE ise (eski/legacy yazım, R03B ÖNCESİ `put_bytes`
          dönüşü): önce `base_dir` içinde mi denenir, sonra (varsa)
          `legacy_base_dir` içinde mi. İkisinin de dışındaysa reddedilir.
          Bu, kurulum dizinindeki (upgrade'de silinecek) eski dosyaların
          upgrade ÖNCESİNDE hâlâ okunabilir kalmasını sağlar; yeni yazım
          ASLA bu dala düşmez (put_bytes artık relative döner).

        Args:
            ref: Local file reference (relative anahtar veya legacy mutlak yol)

        Returns:
            Validated absolute path

        Raises:
            ValueError: If path is outside all recognized roots (path traversal attempt)
        """
        ref_path = Path(ref)

        if not ref_path.is_absolute():
            resolved = (self.base_dir / ref_path).resolve()
            try:
                resolved.relative_to(self.base_dir)
            except ValueError:
                raise ValueError(f"Invalid local ref: path traversal detected ({ref})")
            return str(resolved)

        # Legacy/absolute ref: sırayla base_dir, sonra legacy_base_dir dene.
        resolved = ref_path.resolve()
        for kok in (self.base_dir, self.legacy_base_dir):
            if kok is None:
                continue
            try:
                resolved.relative_to(kok)
                return str(resolved)
            except ValueError:
                continue

        raise ValueError(f"Invalid local ref: path traversal detected ({ref})")

    def migrate_legacy_artifact(self, legacy_ref: str, new_key: str, content_type: str) -> str:
        """
        Bir legacy (kurulum-dizini-göreli) artifact'ı durable köke ATOMİK
        olarak taşır — S5-R03B Bölüm 4 "installer fazına hazırlık".

        ÖNEMLİ: Bu metod BU FAZDA HİÇBİR canlı endpoint/akış tarafından
        ÇAĞRILMAZ; yalnız test altında doğrulanmış, gelecekteki bir installer
        entegrasyonu için hazırlanmış bir PRIMITIF'tir (owner: "R03B
        production veya gerçek legacy dosya üzerinde migration yapmayacak").

        Sözleşme:
        - `legacy_ref` containment'tan geçmelidir (base_dir VEYA
          legacy_base_dir içinde) — aksi hâlde ValueError, hiçbir I/O olmaz.
        - Kopya `put_bytes` (mevcut atomik publish: mkstemp+fsync+os.replace)
          ile yapılır — YENİ bir yazım mekanizması İCAT EDİLMEZ.
        - Kopya SONRASI hash/read-back doğrulaması yapılır (sha256); uyuşmazsa
          yeni yayımlanan dosya GERİ ALINIR (silinir) ve RuntimeError
          yükselir — yarım/bozuk migrasyon durumu kalmaz.
        - KAYNAK DOSYA ASLA SİLİNMEZ (owner: "Kaynak silme ancak ayrı
          installer GO ile"). DB `pdf_ref` güncellemesi de bu metodun
          SORUMLULUĞUNDA DEĞİLDİR — çağıran, dönen `new_key`'i KENDİ DB
          transaction'ı içinde yazmalıdır (storage katmanı DB'ye dokunmaz).

        Args:
            legacy_ref: Taşınacak dosyanın mevcut (legacy) referansı.
            new_key: Durable kökte kullanılacak YENİ relative anahtar.
            content_type: Yeni yazım için content-type (put_bytes'a geçirilir).

        Returns:
            new_key (relative) — DB güncellemesi için çağırana aynen döner.

        Raises:
            ValueError: legacy_ref containment dışındaysa.
            RuntimeError: hash/read-back doğrulaması başarısız olursa.

        Çağrıldığı yerler:
        - (HENÜZ YOK) — gelecekteki installer-fazı migrasyon betiği için
          hazırlanmış primitif; bkz. tests/test_s5_r03b_durable_storage.py
        """
        kaynak_yol = Path(self.resolve_local_path(legacy_ref))
        veri = kaynak_yol.read_bytes()
        kaynak_hash = hashlib.sha256(veri).hexdigest()

        yeni_ref = self.put_bytes(new_key, veri, content_type)

        hedef_yol = Path(self.resolve_local_path(yeni_ref))
        dogrulama_hash = hashlib.sha256(hedef_yol.read_bytes()).hexdigest()

        if dogrulama_hash != kaynak_hash:
            # Rollback: yarım/bozuk migrasyon SONUCU olarak yayımlanmış
            # dosya geri alınır — DB'ye asla yazılmayacak bir ref için
            # orphan dosya bırakılmaz.
            self._temizle(str(hedef_yol))
            try:
                if hedef_yol.exists():
                    hedef_yol.unlink()
            except OSError:
                logger.warning(f"Migrasyon rollback: hedef silinemedi: {hedef_yol}")
            raise RuntimeError(
                f"Migrasyon hash doğrulaması başarısız (kaynak≠hedef): {legacy_ref} -> {new_key}"
            )

        logger.info(f"Legacy artifact migrated: {legacy_ref} -> {new_key} (sha256 doğrulandı, kaynak korundu)")
        return yeni_ref

    def get_local_path(self, ref: str) -> Optional[str]:
        """
        Get validated local path for streaming.

        Returns:
            Local path string or None if invalid
        """
        try:
            return self.resolve_local_path(ref)
        except ValueError:
            return None
