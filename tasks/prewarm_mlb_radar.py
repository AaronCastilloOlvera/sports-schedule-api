"""
Worker nocturno de MLB Radar.

Todo el estado vive en Redis — statsapi.mlb.com es gratuita y sin cuota, así que
re-consultar historia no cuesta nada y no hay nada que persistir en Postgres.

Por liga y por corrida:
  1. Refresca la caché del día + los últimos `RESULT_REFRESH_DAYS` (para que los
     marcadores finales aterricen y `get_accuracy` pueda liquidar los picks).
  2. Asegura la ventana histórica de `HISTORY_DAYS` en `mlb:day:{league}:{fecha}`
     (fechas pasadas son inmutables ⇒ solo se piden las que falten).
  3. Para cada abridor anunciado: game log → perfil (últimas 10 aperturas +
     historial vs el rival de hoy) en `mlb:pitcher:{league}:{id}:{fecha}`.
  4. Calcula los picks y los cachea en `mlb_radar:{league}:{fecha}` (TTL 30 d,
     necesario para medir accuracy después).
  5. Una notificación de Telegram al terminar.
"""
import json
import traceback
from datetime import datetime

import pytz

from utils.redis_client import get_redis_connection
from services.mlb_radar_service import MLBRadarService, PICKS_TTL
from services.notification_service import NotificationService

HISTORY_DAYS = 75          # ventana para park factors, forma de equipo y 1er inning
RESULT_REFRESH_DAYS = 3    # días recientes que se re-piden para capturar finales
LEAGUES_TO_PREWARM = ('mlb', 'lmb')


class MLBRadarPrewarmWorker:
    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()

    def prewarm_mlb_radar(self, date: str = None, leagues=None):
        r, error = get_redis_connection()
        if not r:
            print(f'MLB RADAR PREWARM Redis no disponible ({error}) — saltando')
            return

        today = date or datetime.now(self.local_tz).strftime('%Y-%m-%d')
        summary = []

        for league in (leagues or LEAGUES_TO_PREWARM):
            try:
                svc = MLBRadarService(r, league)
                print(f'MLB RADAR PREWARM [{league}] {today}')

                result = svc.get_suggestions(
                    today,
                    history_days=HISTORY_DAYS,
                    refresh_last=RESULT_REFRESH_DAYS,
                )

                n_games = result['games_analyzed']

                # Se cachea SIEMPRE, incluso sin juegos — de lo contrario /cached
                # da 404 y el front cae al cómputo en vivo de get_suggestions()
                # (4 llamadas externas forzadas, ~5-7s) solo para descubrir que no
                # hay nada que mostrar. Con temporada baja/fuera de temporada esto
                # deja de ser un caso raro. Ver services/mlb_radar_service.py.
                key = f'mlb_radar:{league}:{today}'
                r.setex(key, PICKS_TTL, json.dumps(result, default=str))

                if not n_games:
                    print(f' -> [{league}] sin juegos programados')
                    summary.append(f'{league.upper()}: sin juegos')
                    continue

                n_sug = len(result['suggestions'])
                n_picks = sum(len(s['top_picks']) for s in result['suggestions'])
                print(f' -> [{league}] {n_games} juegos, {n_sug} con picks, '
                      f'{n_picks} picks totales -> {key}')
                summary.append(f'{league.upper()}: {n_sug}/{n_games} juegos, {n_picks} picks')

            except Exception as e:
                print(f'MLB RADAR PREWARM [{league}] Error: {e}')
                traceback.print_exc()
                summary.append(f'{league.upper()}: error')

        self.notification_service.send_message(
            f'✅ Task Executed: MLB Radar {today} — ' + ' · '.join(summary)
        )
