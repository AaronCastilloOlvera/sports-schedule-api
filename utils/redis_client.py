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
    Returns tuple: (connection_object or None, error_message or None)
    """
    try:
        redis_url = os.getenv("REDIS_URL")
        
        # Try to connect using REDIS_URL if available
        if redis_url:
            r = redis.from_url(redis_url, decode_responses=True)
        else:
            # Fallback to individual host/port config
            host = os.getenv("REDIS_HOST", "localhost")
            port = int(os.getenv("REDIS_PORT", 6379))
            db = int(os.getenv("REDIS_DB", 0))
            
            r = redis.Redis(
                host=host,
                port=port,
                db=db,
                decode_responses=True,
                socket_connect_timeout=5,
                socket_keepalive=True
            )
        
        r.ping()
        return r, None
        
    except Exception as e:
        error_msg = f"Redis connection error: {type(e).__name__}: {str(e)}"
        print(error_msg)
        return None, error_msg
