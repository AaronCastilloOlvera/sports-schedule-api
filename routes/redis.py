"""
Routes for Redis cache management endpoints
"""
import json
from fastapi import APIRouter
from utils.redis_client import get_redis_connection

router = APIRouter(prefix="/redis", tags=["redis"])


@router.get("/keys")
def get_redis_keys():
    """
    Get all Redis keys
    """
    try:
        r, error = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed", "details": error}

        keys = r.keys("*")
        sorted_keys = sorted(keys)
        return {"keys": sorted_keys}
    except Exception as e:
        return {"error": "Failed to retrieve Redis keys", "details": str(e)}

@router.get("/{key}")
def get_redis_key(key: str):
    """
    Get a specific Redis key
    """
    try:
        r, error = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed", "details": error}

        value = r.get(key)
        try:
            return json.loads(value)
        except json.JSONDecodeError:
            return value
    except Exception as e:
        return {"error": "Failed to retrieve Redis key", "details": str(e)}

@router.delete("/{key}")
def delete_redis_key(key: str):
    """
    Delete a specific Redis key
    """
    try:
        r, error = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed", "details": error}

        r.delete(key)
        return {"message": f"Key '{key}' deleted successfully"}
    except Exception as e:
        return {"error": "Failed to delete Redis key", "details": str(e)}
