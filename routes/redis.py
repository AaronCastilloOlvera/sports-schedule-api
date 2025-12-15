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
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}

    keys = r.keys("*")
    sorted_keys = sorted(keys)
    return {"keys": sorted_keys}


@router.get("/data")
def get_redis_data():
    """
    Get all Redis data
    """
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}

    keys = r.keys("*")
    result = {}

    for key in keys:
        value = r.get(key)
        try:
            result[key] = json.loads(value)
        except json.JSONDecodeError:
            result[key] = value
    return result


@router.delete("/{key}")
def delete_redis_key(key: str):
    """
    Delete a specific Redis key
    """
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}

    try:
        r.delete(key)
        return {"message": f"Key '{key}' deleted successfully"}
    except Exception as e:
        return {"error": str(e)}
