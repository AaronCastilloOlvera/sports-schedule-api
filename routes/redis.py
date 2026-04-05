import json
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from utils.redis_client import get_redis_connection
from utils import database
from services.sports_api_client import SportsAPIClient
from services.match_service import MatchService

router = APIRouter(prefix="/redis", tags=["redis"])
api_client = SportsAPIClient()

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

@router.get("/get_key_by_id")
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

@router.post("/refresh-fixtures-cache")
def refresh_matches_cache(date: str = Query(..., description="Date in format YYYY-MM-DD"), db: Session = Depends(database.get_db)):
    """
    Refresh the Redis cache for matches on a specific date
    
    Parameters:
    - date: Date in format YYYY-MM-DD (e.g., 2024-12-25)
    """
    try:
      match_service = MatchService(db)
      return match_service.get_matches_by_date(date, force_refresh=True)      
    
    except Exception as e:
        return {"error": "Failed to refresh cache", "details": str(e)}

@router.delete("/delete-key-by-id/{key}")
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

@router.delete("/flushdb")
def flush_redis_db():
    try:
        r, error = get_redis_connection()
        if r is None:
            raise HTTPException(status_code=500, detail=f"Redis connection failed: {error}")
        
        r.flushdb()
        return {"message": "Redis database flushed successfully"}
    except HTTPException as he:
        raise he
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))