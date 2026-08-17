import json
import re
import traceback
from collections import namedtuple
from datetime import datetime
import pytz

from utils.database import SessionLocal
from utils.redis_client import get_redis_connection
from services.bet_radar_service import BetRadarService
from services.notification_service import NotificationService

BET_RADAR_TTL = 7 * 24 * 3600  # 1 week

# Mirror of the frontend findOdd logic — keeps the same bookmaker/pattern preference
_BET_SEARCH = {
    'goals':        {'patterns': [r'goals over/under', r'over/under'],        'books': ['bet365', 'betano', '1xbet']},
    'corners':      {'patterns': [r'corners over under'],                      'books': ['bet365', '1xbet', 'betano']},
    'yellow_cards': {'patterns': [r'yellow over/under', r'cards over/under'], 'books': ['1xbet', 'bet365', 'betano']},
    'btts':         {'patterns': [r'both teams score'],                        'books': ['bet365', 'betano', '1xbet']},
}


def _find_best_odd(odds_data: list, market: str, side: str, line) -> dict | None:
    cfg = _BET_SEARCH.get(market)
    if not cfg or not odds_data:
        return None
    target = ('Yes' if side == 'yes' else 'No') if market == 'btts' \
        else f"{'Over' if side == 'over' else 'Under'} {line}"
    all_bms = [bm for entry in odds_data for bm in entry.get('bookmakers', [])]
    for pref_book in cfg['books']:
        bm = next((b for b in all_bms if b['name'].lower() == pref_book), None)
        if not bm:
            continue
        for pat_str in cfg['patterns']:
            pat = re.compile(pat_str, re.IGNORECASE)
            bet = next((b for b in bm.get('bets', []) if pat.search(b['name'])), None)
            if not bet:
                continue
            v = next((v for v in bet.get('values', []) if str(v['value']) == target), None)
            if v:
                return {'odd': float(v['odd']), 'bookmaker': bm['name']}
    return None


# Lightweight fixture container — same fields ScoutService._analyze_fixture expects
FixtureRow = namedtuple('FixtureRow', [
    'id', 'home_team_id', 'home_team_name',
    'away_team_id', 'away_team_name',
    'home_goals', 'away_goals', 'date_utc',
    'referee', 'league_id', 'status',
])


class BetRadarPrewarmWorker:
    def __init__(self):
        self.local_tz = pytz.timezone('America/Mexico_City')
        self.notification_service = NotificationService()

    def prewarm_scout(self, date: str = None):
        r, _ = get_redis_connection()
        if not r:
            print('BET RADAR PREWARM Redis not available — skipping')
            return

        db = SessionLocal()
        try:
            today = date or datetime.now(self.local_tz).strftime('%Y-%m-%d')
            print(f'BET RADAR PREWARM Running for {today}')

            raw = r.get(f'matches:date:{today}')
            if not raw:
                print('SCOUT PREWARM No schedule in Redis — skipping')
                return

            match_data = json.loads(raw)
            matches = match_data if isinstance(match_data, list) else match_data.get('fixtures', [])

            fixtures = []
            for m in matches:
                date_str = m['fixture'].get('date')
                try:
                    date_utc = datetime.fromisoformat(date_str) if date_str else None
                except ValueError:
                    date_utc = None
                fixtures.append(FixtureRow(
                    id=m['fixture']['id'],
                    home_team_id=m['teams']['home']['id'],
                    home_team_name=m['teams']['home']['name'],
                    away_team_id=m['teams']['away']['id'],
                    away_team_name=m['teams']['away']['name'],
                    home_goals=m.get('goals', {}).get('home'),
                    away_goals=m.get('goals', {}).get('away'),
                    date_utc=date_utc,
                    referee=m['fixture'].get('referee'),
                    league_id=m.get('league', {}).get('id'),
                    status=m['fixture']['status']['short'],
                ))

            print(f' -> {len(fixtures)} fixtures')

            result = BetRadarService(db).get_suggestions_from_list(fixtures, today)

            # Enrich every top_pick with the best available odd from Redis
            for suggestion in result['suggestions']:
                fid = suggestion['fixture_id']
                odds_raw = r.get(f'odds:{fid}')
                odds_data = json.loads(odds_raw) if odds_raw else []
                for pick in suggestion['top_picks']:
                    pick['best_odd'] = _find_best_odd(
                        odds_data, pick['market'], pick['side'], pick.get('line')
                    )

            # Enrich parlay picks (separate objects from top_picks)
            if result.get('parlay_suggestion'):
                for pick in result['parlay_suggestion']['picks']:
                    fid = pick.get('fixture_id')
                    odds_raw = r.get(f'odds:{fid}') if fid else None
                    odds_data = json.loads(odds_raw) if odds_raw else []
                    pick['best_odd'] = _find_best_odd(
                        odds_data, pick['market'], pick['side'], pick.get('line')
                    )

            key = f'bet_radar:{today}'
            r.setex(key, BET_RADAR_TTL, json.dumps(result, default=str))
            n = len(result['suggestions'])
            print(f'SCOUT PREWARM Done — {n} suggestions saved to {key}')
            self.notification_service.send_message(
                f'Scout Best Odds {today}: {n} fixtures con picks'
            )

        except Exception as e:
            print(f'BET RADAR PREWARM Error: {e}')
            traceback.print_exc()
        finally:
            db.close()
