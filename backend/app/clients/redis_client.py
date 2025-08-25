# app/clients/redis_client.py
import redis.asyncio as redis
from app.core import config

_redis = None

def get_redis():
    """
    Retorna un cliente Redis singleton (async).
    Usa REDIS_URL de app/core/config.py
    """
    global _redis
    if _redis is None:
        _redis = redis.from_url(config.REDIS_URL, decode_responses=True)
    return _redis
