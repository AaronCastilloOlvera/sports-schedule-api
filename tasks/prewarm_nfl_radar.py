"""
Worker nocturno de NFL Radar.

Corre TODAS las noches (no solo en días con partido) — el usuario fue explícito:
NFL Radar se revisa cada noche igual que MLB Radar, porque el calendario de la
NFL varía (domingo es lo normal, pero hay Monday/Thursday Night Football y
juegos ocasionales de sábado en fechas tardías de temporada). Si el día no
tiene juegos, el worker simplemente no emite picks — mismo patrón que MLB
Radar en días sin partidos de LMB.

Por corrida:
  1. Re-pide los últimos `RESULT_REFRESH_DAYS` días SOLO si aún tienen algún
     juego sin terminar (para que los marcadores finales aterricen y
     `get_accuracy` pueda liquidar los picks) — evita llamadas de red
     innecesarias a juegos ya inmutables.
  2. Calcula los picks del día de hoy usando todo el historial cacheado
     (`nfl:day:*`) como contexto de equipo, y los cachea en `nfl_radar:{fecha}`
     (TTL 30 d, necesario para medir accuracy después).
  3. Una notificación de Telegram al terminar.
"""
import json
import traceback
from datetime import datetime, timedelta

import pytz

from utils.redis_client import get_redis_connection
from services.nfl_radar_service import NFLRadarService, PICKS_TTL, is_final
from services.notification_service import NotificationService

RESULT_REFRESH_DAYS = 3  # días recientes a re-chequear en busca de finales pendientes


class NFLRadarPrewarmWorker:
    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()

    def prewarm_nfl_radar(self, date: str = None):
        r, error = get_redis_connection()
        if not r:
            print(f'NFL RADAR PREWARM Redis no disponible ({error}) — saltando')
            return

        today = date or datetime.now(self.local_tz).strftime('%Y-%m-%d')
        svc = NFLRadarService(r)

        try:
            print(f'NFL RADAR PREWARM {today}')
            base = datetime.strptime(today, '%Y-%m-%d').date()
            for i in range(1, RESULT_REFRESH_DAYS + 1):
                d = (base - timedelta(days=i)).strftime('%Y-%m-%d')
                cached = svc.get_day(d, force_refresh=False)
                if cached and not all(is_final(g) for g in cached):
                    svc.get_day(d, force_refresh=True)

            result = svc.get_suggestions(today)
            n_games = result['games_analyzed']

            # Se cachea SIEMPRE, incluso sin juegos — de lo contrario /cached da
            # 404 y el front cae al cómputo en vivo de get_suggestions() (varias
            # llamadas externas forzadas) solo para descubrir que no hay nada que
            # mostrar. NFL tiene días sin partido con frecuencia (no todos los
            # días de la semana juegan). Ver services/nfl_radar_service.py.
            key = f'nfl_radar:{today}'
            r.setex(key, PICKS_TTL, json.dumps(result, default=str))

            if not n_games:
                print(f' -> NFL Radar {today}: sin juegos programados')
                self.notification_service.send_message(f'✅ Task Executed: NFL Radar {today} — sin juegos')
                return

            n_sug   = len(result['suggestions'])
            n_picks = sum(len(s['top_picks']) for s in result['suggestions'])
            print(f' -> NFL Radar {today}: {n_games} juegos, {n_sug} con picks, '
                  f'{n_picks} picks totales -> {key}')
            self.notification_service.send_message(
                f'✅ Task Executed: NFL Radar {today} — {n_sug}/{n_games} juegos, {n_picks} picks'
            )
        except Exception as e:
            print(f'NFL RADAR PREWARM Error: {e}')
            traceback.print_exc()
            self.notification_service.send_message(f'⚠️ NFL Radar {today} — error: {e}')
