"""
Local Filesystem Storage Backend.

For development and simple deployments.
"""
import os
import logging
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
    """Local filesystem storage."""

    def __init__(self, base_dir: str | None = None):
        self.base_dir = Path(base_dir or settings.storage_dir).resolve()
        self.base_dir.mkdir(parents=True, exist_ok=True)
        logger.info(f"LocalStorage initialized: {self.base_dir}")

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

        Dönüş sözleşmesi DEĞİŞMEDİ: nihai dosyanın mutlak yolu (str).

        Çağrıldığı yerler:
        - pdf_generator.generate_and_store_offer_pdf() → POST /offers/{id}/generate-pdf
        - contracts.service.upload_reference_document() → POST /contracts/documents/upload
        - contracts.service.finalize_contract_pdf_and_commit() → POST /contracts/{id}/finalize
        - services.pdf_artifact_store.PdfArtifactStore.put() → teklif PDF artifact deposu
        - main.py fatura orijinal/page1 yüklemeleri (POST /extract, /process)
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
        return str(path)  # Local path as reference

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
        the resolved path is within storage_dir.

        S5-R01: `Path.resolve()` Windows'ta symlink/junction/reparse
        point'leri de çözer; containment kontrolü çözülmüş yol üzerinde
        yapıldığı için symlink escape de reddedilir. Karşılaştırma
        `relative_to` ile yapılır — string prefix karşılaştırması DEĞİL
        (aksi hâlde `storage_dir_evil` gibi kardeş dizinler kabul edilirdi).

        Args:
            ref: Local file reference (path)

        Returns:
            Validated absolute path

        Raises:
            ValueError: If path is outside storage_dir (path traversal attempt)
        """
        resolved = Path(ref).resolve()

        # Security check: path must be within base_dir
        try:
            resolved.relative_to(self.base_dir)
        except ValueError:
            raise ValueError(f"Invalid local ref: path traversal detected ({ref})")

        return str(resolved)

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
