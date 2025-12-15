"""
Redis client utility for caching
"""
import redis
import os
from dotenv import load_dotenv

load_dotenv()


def get_redis_connection():
    """
    Get a Redis connection instance
    Returns None if connection fails
    """
    try:
        r = redis.Redis(
            host=os.getenv("REDIS_HOST", "localhost"),
            port=int(os.getenv("REDIS_PORT", 6379)),
            db=int(os.getenv("REDIS_DB", 0)),
            decode_responses=True
        )
        r.ping()
        return r
    except Exception as e:
        print(f"Redis connection error: {e}")
        return None
