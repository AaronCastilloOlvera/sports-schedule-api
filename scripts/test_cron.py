
import os
import json
import requests
from datetime import datetime, timedelta
from dotenv import load_dotenv

import models
from utils.database import SessionLocal
from utils.redis_client import get_redis_connection
from utils.constants import HEADERS

load_dotenv()

def prewarm_cache(days: int):
  print (f"Prewarming cache for next {days} day(s) starting")

  db = SessionLocal()
  r, redis_error = get_redis_connection()

  if r is None:
      print(f"Redis connection failed: {redis_error}")
      return
  
  try:
    favorite_leagues = db.query(models.League).filter(models.League.is_favorite == True).all()
    favorite_ids = [league.league_id for league in favorite_leagues]    

    today = datetime.now()

    for i in range(days):
      target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
      print(f"Prewarming cache for date: {target_date}")

      url = f"https://{os.getenv('API_URL')}/fixtures?"
      params = { "date": target_date, "timezone": "America/Mexico_City" }
      response = requests.get(url, headers=HEADERS, params=params)
      response_data = response.json()
      matches = response_data.get("response", [])

      filtered_data = [
          match for match in matches
          if match.get("league", {}).get("id") in favorite_ids
      ]
      
      r.setex(target_date, json.dumps(filtered_data), ex=432000)
      print(f"Cache prewarmed for date: {target_date} with {len(filtered_data)} matches")
  
    print("Cache prewarming completed")

  except requests.RequestException as e:
    print(f"Error fetching data from external API: {e}")
  except Exception as e:
    print(f"Error prewarming cache: {e}")
  finally:
    db.close()

if __name__ == "__main__":
  prewarm_cache(days=5)

