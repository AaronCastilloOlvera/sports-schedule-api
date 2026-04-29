import os
import requests
from fastapi import APIRouter, HTTPException

router = APIRouter(prefix="/api/status", tags=["status"])


@router.get("/usage")
def get_api_usage():
    api_key = os.getenv("API_KEY")
    api_url = os.getenv("API_URL")

    if not api_key or not api_url:
        raise HTTPException(
            status_code=500,
            detail="API_KEY or API_URL is not set in environment variables.",
        )

    url = f"https://{api_url}/status"
    headers = {
        "x-rapidapi-key": api_key,
        "x-rapidapi-host": api_url,
    }

    try:
        response = requests.get(url, headers=headers, timeout=10)
        response.raise_for_status()
        req = response.json().get("response", {}).get("requests", {})

        current = req.get("current", 0)
        limit_day = req.get("limit_day", 0)

        return {
            "requests": {
                "current": current,
                "limit_day": limit_day,
                "remaining": limit_day - current,
            }
        }

    except requests.Timeout:
        raise HTTPException(status_code=500, detail="External API request timed out.")
    except requests.RequestException as e:
        raise HTTPException(
            status_code=500,
            detail=f"Failed to reach external API: {str(e)}",
        )
