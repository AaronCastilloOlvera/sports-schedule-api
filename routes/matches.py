"""
Routes for Match-related endpoints
"""
import os
import json
import requests
from datetime import datetime
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from utils.constants import HEADERS
from utils.redis_client import get_redis_connection
from utils import database
import models

load_dotenv()

router = APIRouter(prefix="/matches", tags=["matches"])

@router.get("/headtohead")
def get_headtohead_matches(
  team1: int = Query(..., description="ID of the first team"),
  team2: int = Query(..., description="ID of the second team")):
  """
  Get head-to-head matches between two teams
  """
  try:
      r, _ = get_redis_connection()
      
      t1, t2 = sorted([team1, team2])
      cache_key = f"h2h:{t1}&{t2}"

      if r:
          cached_data = r.get(cache_key)
          if cached_data:
              return {
                  "from_cache": True,
                  "matches": json.loads(cached_data)
              }

      url = f"https://{os.getenv('API_URL')}/fixtures/headtohead"
      params = { "h2h": f"{team1}-{team2}" }
      response = requests.get(url, headers=HEADERS, params=params)
      response_data = response.json()
      api_data = response_data.get("response", [])
      
      if r:
          r.set(cache_key, json.dumps(api_data), ex=3600)

      return {"matches": api_data, "from_cache": False}
  except requests.RequestException as e:
      return {"error": "Failed to fetch from external API", "details": str(e)}
  except Exception as e:
      return {"error": "Failed to process head-to-head matches", "details": str(e)}

@router.get("/headtohead/cached-keys")
def get_h2h_cached_keys():
    """
    Get all cache keys for head-to-head matches from Redis.
    """
    r, redis_error = get_redis_connection()
    if not r:
        return {"error": "Redis connection failed", "details": redis_error}

    try:
        keys = list(r.scan_iter(match="h2h:*"))
        return keys
    except Exception as e:
        return {"error": "Failed to scan keys from Redis", "details": str(e)}
    
@router.get("/by-date")
def get_matches_by_date(date: str = Query(..., description="Date in format YYYY-MM-DD"), db: Session = Depends(database.get_db)):
    """
    Get matches for a specific date, filtered by favorite leagues
    
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
        
        # Check cache if Redis is available
        if r is not None:
            cached_data = r.get(str(date))
            if cached_data:
                return {"data": json.loads(cached_data), "cached": True}

        # Fetch from external API
        url = f"https://{os.getenv('API_URL')}/fixtures?"
        params = { "date": date, "timezone": "America/Mexico_City" }
        response = requests.get(url, headers=HEADERS, params=params)
        response_data = response.json()
        matches = response_data.get("response", [])

        # Filter by favorite leagues
        favorite_leagues = db.query(models.League).filter(models.League.is_favorite == True).all()
        favorite_ids = {league.id for league in favorite_leagues}

        filtered_data = [
            match for match in matches
            if match.get("league", {}).get("id") in favorite_ids
        ]

        # Cache the result if Redis is available
        if r is not None:
            r.set(date, json.dumps(filtered_data))

        return {"data": filtered_data, "cached": False}

    except requests.RequestException as e:
        return {"error": "Failed to fetch from external API", "details": str(e)}
    except Exception as e:
        return {"error": "Failed to process matches", "details": str(e)}
