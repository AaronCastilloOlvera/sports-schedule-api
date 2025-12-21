"""
Routes for Redis cache management endpoints
"""
import json
from fastapi import APIRouter, HTTPException
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
    try:
        r, error = get_redis_connection()
        if r is None:
            raise HTTPException(status_code=500, detail=f"Redis connection failed: {error}")

        raw_value = r.get(key)

        if raw_value is None:
            raise HTTPException(status_code=404, detail=f"Key '{key}' not found")
        
        if isinstance(raw_value, bytes):
            raw_value = raw_value.decode("utf-8")
            
        try:
            return json.loads(raw_value)
        except (json.JSONDecodeError, TypeError):
            return raw_value

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@router.delete("/{key}")
def delete_redis_key(key: str):
    try:
        r, error = get_redis_connection()
        if r is None:
            raise HTTPException(status_code=500, detail=f"Redis connection failed: {error}")
        
        result = r.delete(key)
        
        if result == 0:
            raise HTTPException(status_code=404, detail=f"Key '{key}' did not exist")

        return {"message": f"Key '{key}' deleted successfully"}

    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))
