"""
Routes for Redis cache management endpoints
"""
from datetime import datetime
import json
import os
import requests
from fastapi import APIRouter, HTTPException, Query, Depends
from sqlalchemy.orm import Session
from utils.constants import HEADERS
from utils.redis_client import get_redis_connection
from utils import database
import models

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


@router.post("/refresh-fixtures-cache")
def refresh_matches_cache(date: str = Query(..., description="Date in format YYYY-MM-DD"), db: Session = Depends(database.get_db)):
    """
    Refresh the Redis cache for matches on a specific date
    
    Parameters:
    - date: Date in format YYYY-MM-DD (e.g., 2024-12-25)
    """
    try:
        # Validate date format
        try:
            datetime.strptime(date, "%Y-%m-%d")
        except ValueError:
            return {
                "error": "Invalid date format",
                "details": "Date must be in YYYY-MM-DD format (e.g., 2024-12-25)"
            }
        
        r, redis_error = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed", "details": redis_error}

        # Fetch from external API
        url = f"https://{os.getenv('API_URL')}/fixtures?"
        params = {"date": date}
        response = requests.get(url, headers=HEADERS, params=params)
        response_data = response.json()
        matches = response_data.get("response", [])

        print("Fetched matches from external API:", matches)

        # Filter by favorite leagues
        favorite_leagues = db.query(models.League).filter(models.League.is_favorite == True).all()
        favorite_ids = {league.id for league in favorite_leagues}

        filtered_data = [
            match for match in matches
            if match.get("league", {}).get("id") in favorite_ids
        ]

        # Update cache
        r.set(date, json.dumps(filtered_data))

        return {"data": filtered_data, "cached": False, "message": "Cache refreshed successfully"}

    except requests.RequestException as e:
        return {"error": "Failed to fetch from external API", "details": str(e)}
    except Exception as e:
        return {"error": "Failed to refresh cache", "details": str(e)}