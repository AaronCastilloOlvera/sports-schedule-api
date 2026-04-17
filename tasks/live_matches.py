from datetime import datetime, timedelta, timezone
from dotenv import load_dotenv
from dateutil import parser
from utils.database import SessionLocal
from services.match_service import MatchService
import os
import pytz

load_dotenv()
minutes_interval = int(os.getenv("WORKER_INTERVAL_MINUTES", 5))

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
        
        # Bypasses Redis cache, fetches from API, and updates Redis
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
        """Dibuja una línea de tiempo en ASCII de 24 horas (resolución de 30 min)."""
        print("\n" + "="*56)
        print("📊 LÍNEA DE TIEMPO DEL WORKER (Hora Local) 📊")
        print("="*56)
        
        if not self.active_windows:
            print("💤 No hay partidos hoy. El worker dormirá todo el día.\n")
            return

        # Creamos 48 bloques de 30 minutos para representar las 24 horas del día
        timeline = ["."] * 48
        hoy_fecha = datetime.now(self.local_tz).date()

        for start, end in self.active_windows:
            start_local = start.astimezone(self.local_tz)
            end_local = end.astimezone(self.local_tz)

            # Calculamos el índice de inicio (0 a 47)
            if start_local.date() < hoy_fecha:
                start_idx = 0  # Si empezó ayer, graficamos desde la medianoche de hoy
            elif start_local.date() > hoy_fecha:
                continue       # Si empieza mañana, lo ignoramos hoy
            else:
                start_idx = (start_local.hour * 60 + start_local.minute) // 30

            # Calculamos el índice de fin (0 a 47)
            if end_local.date() > hoy_fecha:
                end_idx = 47   # Si termina mañana, graficamos hasta las 23:59 de hoy
            elif end_local.date() < hoy_fecha:
                continue       # Si terminó ayer, lo ignoramos
            else:
                end_idx = (end_local.hour * 60 + end_local.minute) // 30

            # "Pintamos" los bloques activos
            for i in range(start_idx, min(end_idx + 1, 48)):
                timeline[i] = "█"

        # Construimos el string visual de las horas (00 a 23)
        escala_horas = "".join([f"{h:02d} " for h in range(24)])
        
        # Construimos el string de los bloques (juntamos 2 de 30 min por cada hora)
        grafica = ""
        for i in range(0, 48, 2):
            b1 = timeline[i]
            b2 = timeline[i+1]
            grafica += f"{b1}{b2} "

        print("Horas:  " + escala_horas)
        print("Activo: " + grafica)
        print("-" * 56)
        print("Leyenda: █ = Worker Activo (Fetch a la API) | . = Durmiendo")
        print("="*56 + "\n")