"""
Redis RQ Adapter - Opsiyonel Redis entegrasyonu.

DB job tablosu "source of truth" kalır.
Redis sadece hızlı dispatch için kullanılır.

Kullanım:
    - REDIS_URL set edilmişse: Job DB'ye yazılır + Redis'e push edilir
    - REDIS_URL yoksa: Sadece DB polling (mevcut davranış)
"""
import logging
from typing import Optional

from .database import REDIS_URL

logger = logging.getLogger(__name__)

# Redis bağlantısı (lazy init)
_redis_conn = None
_rq_queue = None


def get_redis_connection():
    """Redis bağlantısı al (lazy)."""
    global _redis_conn
    if _redis_conn is None and REDIS_URL:
        try:
            from redis import Redis
            _redis_conn = Redis.from_url(REDIS_URL)
            _redis_conn.ping()  # Bağlantı testi
            logger.info(f"Redis connected: {REDIS_URL}")
        except Exception as e:
            logger.warning(f"Redis connection failed: {e}")
            _redis_conn = None
    return _redis_conn


def get_rq_queue():
    """RQ Queue al (lazy)."""
    global _rq_queue
    if _rq_queue is None:
        conn = get_redis_connection()
        if conn:
            try:
                from rq import Queue
                _rq_queue = Queue("jobs", connection=conn)
                logger.info("RQ Queue initialized")
            except Exception as e:
                logger.warning(f"RQ Queue init failed: {e}")
                _rq_queue = None
    return _rq_queue


def is_redis_enabled() -> bool:
    """Redis aktif mi?"""
    return REDIS_URL is not None and get_redis_connection() is not None


def enqueue_to_redis(job_id: str) -> bool:
    """
    Job ID'yi Redis kuyruğuna ekle.
    
    Returns:
        True: Başarıyla eklendi
        False: Redis yok veya hata
    """
    if not REDIS_URL:
        return False
    
    queue = get_rq_queue()
    if not queue:
        return False
    
    try:
        # RQ job oluştur - worker'da run_job fonksiyonu çağrılacak
        queue.enqueue(
            "app.rq_worker.run_job",
            job_id,
            job_id=f"job_{job_id}"  # RQ job ID (duplicate prevention)
        )
        logger.info(f"Job {job_id} enqueued to Redis")
        return True
    except Exception as e:
        logger.error(f"Redis enqueue failed for job {job_id}: {e}")
        return False


def get_queue_stats() -> Optional[dict]:
    """Redis kuyruk istatistikleri."""
    if not is_redis_enabled():
        return None
    
    queue = get_rq_queue()
    if not queue:
        return None
    
    try:
        return {
            "name": queue.name,
            "count": len(queue),
            "is_empty": queue.is_empty()
        }
    except Exception as e:
        logger.error(f"Queue stats error: {e}")
        return None
