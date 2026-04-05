from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from utils.database import SessionLocal
from services.match_service import MatchService
from services.notification_service import NotificationService
import time

load_dotenv()

# This script is used to prewarm the Redis cache.

def prewarm_cache(days: int):
  print (f"Prewarming cache for next {days} day(s) starting")
  db = SessionLocal()
  notification_service = NotificationService()
  
  total_matches_stored = 0
  
  try:
    match_service = MatchService(db)
    today = datetime.now(timezone.utc)

    for i in range(days):
      target_date = (today + timedelta(days=i)).strftime("%Y-%m-%d")

      matches = match_service.get_matches_by_date(target_date, force_refresh=True)
      total_matches_stored += len(matches.get("data", []))

      print(f"Fetched {len(matches)} matches for date: {target_date}")

      time.sleep(5)  # Sleep to avoid overwhelming the database or API
  
    message = f"✅ Cache prewarming completed for {days} day(s). Total matches stored: {total_matches_stored}."
    notification_service.send_message(message)
    print(message)
    
  except Exception as e:
    print(f"🚨Error during cache prewarming: {str(e)}")
  finally:
    db.close()

if __name__ == "__main__":
  prewarm_cache(days=2)