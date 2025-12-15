"""
Routes for Match-related endpoints
"""
import os
import json
import requests
from fastapi import APIRouter
from dotenv import load_dotenv
from utils.redis_client import get_redis_connection

load_dotenv()

router = APIRouter(prefix="/matches", tags=["matches"])

# Headers for external API requests
HEADERS = {
    "x-rapidapi-host": os.getenv("API_URL"),
    "x-rapidapi-key": os.getenv("API_KEY")
}

# Constants
FAVORITE_LEAGUES = [2, 39, 61, 71, 78, 135, 140, 262]


@router.get("/by-date/{date}")
def get_matches_by_date(date: str):
    """
    Get matches for a specific date, filtered by favorite leagues
    """
    try:
        r = get_redis_connection()
        if r is None:
            return {"error": "Redis connection failed"}

        # Check cache
        cached_data = r.get(str(date))
        if cached_data:
            return json.loads(cached_data)

        # Fetch from external API
        url = f"https://{os.getenv('API_URL')}/fixtures?"
        params = {"date": date}
        response = requests.get(url, headers=HEADERS, params=params)
        response_data = response.json()
        matches = response_data.get("response", [])

        # Filter by favorite leagues
        filtered_data = [
            match for match in matches
            if match.get("league", {}).get("id") in FAVORITE_LEAGUES
        ]

        # Cache the result
        r.set(date, json.dumps(filtered_data))

        return filtered_data

    except Exception as e:
        return {"error": "Failed to fetch matches", "details": str(e)}
