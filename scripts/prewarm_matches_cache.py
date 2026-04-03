from datetime import datetime, timedelta
from dotenv import load_dotenv
from utils.database import SessionLocal
from services.match_service import MatchService

load_dotenv()

# This script is used to prewarm the Redis cache.

def prewarm_cache(days: int):
  print (f"Prewarming cache for next {days} day(s) starting")
  db = SessionLocal()
  
  try:
    match_service = MatchService(db)
    today = datetime.now(datetime.timezone.utc).date()

    for i in range(days):
      target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")
      matches = match_service.get_matches_by_date(target_date, force_refresh=True)
      print(f"Fetched {len(matches)} matches for date: {target_date}")
  
    print("Cache prewarming completed")
  except Exception as e:
    print(f"Error prewarming cache: {e}")
  finally:
    db.close()

if __name__ == "__main__":
  prewarm_cache(days=5)