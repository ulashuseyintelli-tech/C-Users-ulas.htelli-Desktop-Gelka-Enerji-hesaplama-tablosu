"""
Rate Limiter - Sliding window with Redis support.

MVP: In-memory (single instance)
Prod: Redis-based limiter for multi-instance deployments.

Usage:
    from app.services.rate_limit import check_rate_limit, RateLimitExceeded
    
    try:
        check_rate_limit(f"api_key:{api_key}")
    except RateLimitExceeded:
        raise HTTPException(status_code=429, detail="Rate limit exceeded")
"""
import time
import logging
from collections import defaultdict, deque
from typing import Optional

from app.core.config import settings

logger = logging.getLogger(__name__)

# In-memory buckets: key -> deque of timestamps
_BUCKETS: dict[str, deque[float]] = defaultdict(deque)

# Redis client (lazy init)
_redis_client = None


class RateLimitExceeded(Exception):
    """Raised when rate limit is exceeded."""
    def __init__(self, key: str, limit: int, window: int = 60):
        self.key = key
        self.limit = limit
        self.window = window
        super().__init__(f"Rate limit exceeded for {key}: {limit} requests per {window}s")


def _get_redis_client():
    """Get or create Redis client."""
    global _redis_client
    
    if _redis_client is not None:
        return _redis_client
    
    redis_url = getattr(settings, 'redis_url', None)
    if not redis_url:
        return None
    
    try:
        import redis
        _redis_client = redis.from_url(redis_url, decode_responses=True)
        # Test connection
        _redis_client.ping()
        logger.info(f"Redis rate limiter connected: {redis_url}")
        return _redis_client
    except Exception as e:
        logger.warning(f"Redis connection failed, using in-memory rate limiter: {e}")
        return None


def _check_rate_limit_redis(
    key: str,
    limit: int,
    window: float
) -> None:
    """
    Redis-based sliding window rate limiter.
    Uses sorted sets with timestamps as scores.
    """
    client = _get_redis_client()
    if not client:
        # Fallback to in-memory
        return _check_rate_limit_memory(key, limit, window)
    
    now = time.time()
    redis_key = f"ratelimit:{key}"
    
    try:
        pipe = client.pipeline()
        
        # Remove old entries outside window
        pipe.zremrangebyscore(redis_key, 0, now - window)
        
        # Count current entries
        pipe.zcard(redis_key)
        
        # Add current request
        pipe.zadd(redis_key, {str(now): now})
        
        # Set expiry on key
        pipe.expire(redis_key, int(window) + 1)
        
        results = pipe.execute()
        current_count = results[1]
        
        if current_count >= limit:
            # Remove the entry we just added
            client.zrem(redis_key, str(now))
            logger.warning(f"Rate limit exceeded (Redis) for {key}: {current_count}/{limit}")
            raise RateLimitExceeded(key, limit, int(window))
            
    except RateLimitExceeded:
        raise
    except Exception as e:
        logger.error(f"Redis rate limit error, falling back to memory: {e}")
        return _check_rate_limit_memory(key, limit, window)


def _check_rate_limit_memory(
    key: str,
    limit: int,
    window: float
) -> None:
    """
    In-memory sliding window rate limiter.
    """
    now = time.time()
    q = _BUCKETS[key]
    
    # Remove timestamps outside the window
    while q and (now - q[0]) > window:
        q.popleft()
    
    # Check limit
    if len(q) >= limit:
        logger.warning(f"Rate limit exceeded (memory) for {key}: {len(q)}/{limit}")
        raise RateLimitExceeded(key, limit, int(window))
    
    # Record this request
    q.append(now)


def check_rate_limit(
    key: str,
    limit: Optional[int] = None,
    window: float = 60.0
) -> None:
    """
    Check and update rate limit for a key.
    
    Uses Redis if available, falls back to in-memory.
    
    Args:
        key: Unique identifier (e.g., "api_key:dev-key", "tenant:lima")
        limit: Max requests per window (default: settings.rate_limit_per_minute)
        window: Time window in seconds (default: 60)
    
    Raises:
        RateLimitExceeded: If limit exceeded
    """
    if not settings.rate_limit_enabled:
        return
    
    if limit is None:
        limit = settings.rate_limit_per_minute
    
    # Try Redis first, fallback to memory
    redis_url = getattr(settings, 'redis_url', None)
    if redis_url:
        return _check_rate_limit_redis(key, limit, window)
    else:
        return _check_rate_limit_memory(key, limit, window)


def get_rate_limit_status(key: str, window: float = 60.0) -> dict:
    """
    Get current rate limit status for a key.
    
    Returns:
        {remaining: int, limit: int, reset_in: float}
    """
    limit = settings.rate_limit_per_minute
    now = time.time()
    q = _BUCKETS.get(key, deque())
    
    # Count requests in window
    count = sum(1 for ts in q if (now - ts) <= window)
    
    # Calculate reset time
    reset_in = 0.0
    if q:
        oldest_in_window = next((ts for ts in q if (now - ts) <= window), None)
        if oldest_in_window:
            reset_in = window - (now - oldest_in_window)
    
    return {
        "remaining": max(0, limit - count),
        "limit": limit,
        "reset_in": round(reset_in, 1)
    }


def clear_rate_limit(key: str) -> None:
    """Clear rate limit bucket for a key (for testing)."""
    if key in _BUCKETS:
        _BUCKETS[key].clear()


def clear_all_rate_limits() -> None:
    """Clear all rate limit buckets (for testing)."""
    _BUCKETS.clear()
