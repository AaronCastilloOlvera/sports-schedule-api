"""
Routes for Match-related endpoints
"""
import os
import json
import requests
from datetime import datetime
from fastapi import APIRouter, Query
from dotenv import load_dotenv
from utils.redis_client import get_redis_connection
from .leagues import FAVORITE_LEAGUES

load_dotenv()

router = APIRouter(prefix="/matches", tags=["matches"])

# Headers for external API requests
HEADERS = {
    "x-rapidapi-host": os.getenv("API_URL"),
    "x-rapidapi-key": os.getenv("API_KEY")
}

@router.get("/by-date")
def get_matches_by_date(date: str = Query(..., description="Date in format YYYY-MM-DD")):
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
        favorite_ids = {league["id"] for league in FAVORITE_LEAGUES}

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
