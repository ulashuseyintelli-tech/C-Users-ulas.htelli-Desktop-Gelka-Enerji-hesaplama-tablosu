"""İzole-Session okuma yardımcısı — orphan-thread × request-Session yarışını eler.

SORUN (Python 3.13.14 ile ampirik doğrulandı; PR #62 pre-merge incelemesi):
    Kritik-yol DB okumaları `_get_wrapper("db_primary"|"db_replica").call(...)`
    üzerinden `asyncio.to_thread` ile çalıştırılır. Wrapper (guards/dependency_wrapper.py)
    `asyncio.wait_for(fn(*args), timeout=...)` kullanır. Burada `fn` = `asyncio.to_thread`.
    Zaman aşımında `wait_for` beklenen coroutine'i İPTAL eder, AMA `to_thread`'in
    çalıştırdığı senkron worker THREAD'İ İPTAL EDİLEMEZ: TimeoutError yükselirken
    thread arka planda (orphan) çalışmaya DEVAM eder.

    Eğer bu orphan thread, isteğin `Depends(get_db)` ile açılan REQUEST Session'ını
    kullanıyorsa, `get_db`'nin `finally`'de o Session'ı KAPATMASI ile orphan'ın
    Session'ı kullanması arasında (a) iş parçacıkları-arası Session kullanımı ve
    (b) kapalı-Session erişimi yarışı SOMUT oluşur. PR #62 bunu yalnız epias-compare
    ucunda çözdü; aynı yarış TÜM `db_(primary|replica)` + to_thread okuma uçlarında
    yapısal olarak mevcuttu.

ÇÖZÜM (PR #62 deseniyle tutarlı, dar):
    Kritik-yol okumasını REQUEST Session ile DEĞİL, request Session'ın engine'ine
    (`db.get_bind()`) bağlı KISA ÖMÜRLÜ ayrı bir Session'da yap; bu Session'ı `finally`'de
    KAPAT. Böylece orphan thread YALNIZ kendi Session'ına (ve kendi bağlantısına)
    dokunur; request Session'ı hiç görmez → yarış yapısal olarak ELENİR.

DETACHED-SAFETY SÖZLEŞMESİ (çağıran uymalı):
    İzole Session `finally`'de kapandığında, `reader`'ın döndürdüğü ORM nesneleri
    detached olur. Kapatma (`close`) o an YÜKLENMİŞ skaler sütunları expire ETMEZ;
    dolayısıyla önceden yüklenmiş skaler kolonlara erişim güvenlidir. Ama lazy-load
    ilişkiler / deferred kolonlar DetachedInstanceError verir. Bu nedenle `reader`
    YA düz değer/dataclass/dict döndürmeli, YA da yalnız okuma anında yüklenmiş skaler
    kolonları sonradan okunacak ORM nesneleri döndürmeli (ilişki/lazy-load YOK).
    Bu modülü kullanan tüm uçların çağırdığı okuma fonksiyonları bu sözleşmeye göre
    incelendi (bkz. main.py'deki ilgili uç yorumları).
"""

from typing import Any, Callable, TypeVar

from sqlalchemy.orm import Session

T = TypeVar("T")


def run_read_in_isolated_session(bind: Any, reader: Callable[[Session], T]) -> T:
    """`reader(session)` okumasını `bind`'e bağlı kısa ömürlü izole Session'da çalıştır.

    Args:
        bind: Request Session'ının engine'i (`db.get_bind()`). Yeni Session bundan
            kendi bağlantısını açar; request Session'ın bağlantısı KULLANILMAZ.
        reader: İzole Session'ı alıp SALT OKUMA yapan ve detached-safe bir sonuç
            döndüren çağrılabilir (bkz. modül docstring'i, DETACHED-SAFETY).

    Returns:
        `reader`'ın döndürdüğü değer.

    NOT: `reader` içinde COMMIT/YAZMA yapılmaz — bu yardımcı yalnız kritik-yol
    OKUMALARI içindir (yazma/kesinleştirme uçları kapsam DIŞI: onların yarış
    profili ve idempotens sözleşmesi farklıdır).
    """
    session = Session(bind=bind)
    try:
        return reader(session)
    finally:
        session.close()
