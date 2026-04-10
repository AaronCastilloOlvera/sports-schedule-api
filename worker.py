from datetime import datetime, timedelta, timezone
import pytz
from dateutil import parser
from apscheduler.schedulers.blocking import BlockingScheduler
from utils.database import SessionLocal
from services.match_service import MatchService

class LiveWorker:
    def __init__(self):
        # We keep your local timezone specifically for fetching "Today's" schedule correctly
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.active_windows = []
        self.active_games_pending = False
        self.IN_PLAY_STATUSES = {'1H', 'HT', '2H', 'ET', 'BT', 'P', 'LIVE', 'INT'}

    def run_scout(self):
        """Calculates today's match schedules and creates active time windows"""
        print("[SCOUT] 🕵️‍♂️ Searching for today's matches...")
        db = SessionLocal()
        try:
            match_service = MatchService(db)
            
            # CRITICAL: We request 'today' based on local time, not UTC!
            today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
            
            response = match_service.get_matches_by_date(today_str)
            matches = response.get("data", [])
            
            if not matches:
                print("[SCOUT] 💤 No matches scheduled for today.")
                self.active_windows = []
                return

            # Calculate windows (start time + 3 hours buffer for extra time/penalties)
            windows = []
            for match in matches:
                # API returns dates in UTC ISO format, parser handles it correctly
                start_time = parser.parse(match["fixture"]["date"])
                windows.append([start_time, start_time + timedelta(hours=3)])
                
            # Algorithm: Merge overlapping windows
            windows.sort(key=lambda x: x[0])
            merged_windows = []
            for w in windows:
                if not merged_windows:
                    merged_windows.append(w)
                else:
                    last_window = merged_windows[-1]
                    if w[0] <= last_window[1]: # Overlapping condition
                        last_window[1] = max(last_window[1], w[1])
                    else:
                        merged_windows.append(w)
                        
            self.active_windows = merged_windows
            print(f"[SCOUT] ✅ Planning complete. {len(merged_windows)} active windows for today.")

            for idx, (start_time, end_time) in enumerate(merged_windows, start=1):
                start_local = start_time.astimezone(self.local_tz).strftime("%H:%M")
                end_local = end_time.astimezone(self.local_tz).strftime("%H:%M")
                print(f" 🕒 ActiveWindow {idx}: {start_local} - {end_local} (Local Time)")
            
        except Exception as e:
            print(f"[SCOUT ERROR] ❌ {str(e)}")
        finally:
            db.close()

    def run_live_update(self):
        """Checks the API if we are within an active window or games are still pending"""
        current_time_utc = datetime.now(timezone.utc)
        
        is_scheduled_time = any(start <= current_time_utc <= end for start, end in self.active_windows)
        
        if is_scheduled_time or self.active_games_pending:
            print(f"[WORKER] ⚽ In-play window active (Current UTC: {current_time_utc.strftime('%H:%M:%S')}). Fetching data...")
            db = SessionLocal()
            try:
                match_service = MatchService(db)
                today_str = datetime.now(self.local_tz).strftime("%Y-%m-%d")
                
                # force_refresh=True bypasses Redis cache, fetches from API, and updates Redis
                response = match_service.get_matches_by_date(today_str, force_refresh=True)
                matches = response.get("data", [])
                
                # Check if ANY match is currently being played
                self.active_games_pending = any(
                    m.get("fixture", {}).get("status", {}).get("short") in self.IN_PLAY_STATUSES
                    for m in matches
                )
                print(f"[WORKER] ✅ Update finished. Pending active games: {self.active_games_pending}")
                
            except Exception as e:
                print(f"[WORKER ERROR] ❌ {str(e)}")
            finally:
                db.close()
        else:
            print("[WORKER] 🌙 Outside of match hours. Sleeping...")

def start_worker():
    print("========================================")
    print("🚀 STARTING SPORTS SCHEDULE WORKER 🚀")
    print("========================================")
    
    worker = LiveWorker()
    scheduler = BlockingScheduler()
    
    # 1. The Scout runs every day at 6:00 AM UTC
    scheduler.add_job(worker.run_scout, 'cron', hour=6, minute=0, timezone=timezone.utc)
    
    # 2. The Worker runs every 5 minutes, ALL DAY
    scheduler.add_job(worker.run_live_update, 'interval', minutes=5)
    
    # Manually run the scout on startup (useful for deployments during the day)
    worker.run_scout()
    
    try:
        # This blocks the main thread and keeps the container alive forever
        scheduler.start()
    except (KeyboardInterrupt, SystemExit):
        print("Shutting down Worker...")

if __name__ == "__main__":
    start_worker()