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


@router.get("/data")
def get_redis_data():
    """
    Get all Redis data
    """
    try:
        r, error = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed", "details": error}

        keys = r.keys("*")
        result = {}

        for key in keys:
            value = r.get(key)
            try:
                result[key] = json.loads(value)
            except json.JSONDecodeError:
                result[key] = value
        return result
    except Exception as e:
        return {"error": "Failed to retrieve Redis data", "details": str(e)}


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
