"""
In-memory, per-process rate limiter. Deliberately simple: a fixed-window
counter keyed by (bucket, key), no external dependency.

HONEST LIMITATION: this state lives in process memory. It resets on every
restart/redeploy, and does NOT share state across multiple instances if
you ever scale this service horizontally - each instance would enforce
its own independent limit, effectively multiplying the real limit by
instance count. Fine for a single free-tier Render instance (the normal
case for this project). If you outgrow a single instance, replace this
with a Redis/Upstash-backed limiter (same interface, swap the storage) -
noted here rather than pretending this is production-grade at scale.
"""
import time
from collections import defaultdict
from threading import Lock

_lock = Lock()
_buckets: dict[str, list[float]] = defaultdict(list)


def is_allowed(key: str, limit: int, window_seconds: int) -> bool:
    """
    Sliding window: True if fewer than `limit` calls for this key happened
    in the last `window_seconds`. Records this call as a hit if allowed.
    """
    now = time.time()
    with _lock:
        hits = _buckets[key]
        cutoff = now - window_seconds
        # drop hits outside the window
        while hits and hits[0] < cutoff:
            hits.pop(0)

        if len(hits) >= limit:
            return False

        hits.append(now)
        return True


def current_count(key: str, window_seconds: int) -> int:
    now = time.time()
    with _lock:
        hits = _buckets[key]
        cutoff = now - window_seconds
        return sum(1 for h in hits if h >= cutoff)
