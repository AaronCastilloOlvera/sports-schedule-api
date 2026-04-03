import os
import json
import requests
from fastapi import APIRouter, Query, Depends
from sqlalchemy.orm import Session
from dotenv import load_dotenv
from utils.constants import HEADERS
from utils.redis_client import get_redis_connection
from utils import database
from services.sports_api_client import SportsAPIClient
from services.match_service import MatchService

load_dotenv()

router = APIRouter(prefix="/matches", tags=["matches"])
api_client = SportsAPIClient()

@router.get("/by-date")
def get_matches_by_date(date: str = Query(..., description="Date in format YYYY-MM-DD"), db: Session = Depends(database.get_db)):
  match_service = MatchService(db)
  return match_service.get_matches_by_date(date, force_refresh=True)

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


