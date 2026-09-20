"""
NFL Radar — motor de picks de fútbol americano (NFL), contra la línea de DraftKings.

Redis-only, mismo espíritu que MLB Radar: no hay tablas ni migraciones. A
diferencia de MLB (sin momios) y de BetRadar de fútbol (momios parciales), aquí
`site.api.espn.com` expone momios reales de casa de apuestas (`pickcenter`) para
cada juego, antes y después del kickoff. El trabajo del motor NO es elegir una
línea — la pone DraftKings — sino estimar si la probabilidad implícita del
mercado está equivocada, y por cuánto (mismo patrón que `ValuePicksTab` del
frontend: edge = nuestra_prob − prob_implícita_del_mercado).

Claves Redis que este servicio posee (nadie más escribe en ellas):

  nfl:day:{YYYY-MM-DD}              30 d  schedule + score + boxscore-lite + odds del día
  nfl_radar:{YYYY-MM-DD}            30 d  picks calculados del día
  nfl_radar:accuracy:{end}:{...}     1 h  respuesta de get_accuracy

Todos los picks salen con la forma del feed de fútbol/MLB:
  {market, label, note, confidence, side, line, samples, odd}
`odd` es el momio DECIMAL real del lado recomendado (vía `utils/odds.py:normalize_odds`)
— a diferencia de MLB, aquí sí existe fuente de momios.
"""
import json
import math
import time
from collections import defaultdict
from datetime import datetime, timedelta

import pytz

from services.nfl_api_client import NFLApiClient
from utils.odds import normalize_odds

REQUEST_SLEEP = 0.3  # ESPN endpoint no es oficial — ser respetuoso entre llamadas secuenciales

# ── TTLs ──────────────────────────────────────────────────────────────────────
DAY_TTL      = 30 * 24 * 3600   # 30 d — un juego terminado es inmutable
PICKS_TTL    = 30 * 24 * 3600   # 30 d — necesario para medir accuracy después
ACCURACY_TTL = 3600             # 1 h
SCHEDULE_TTL = 120               # 2 min — marcador en vivo; separado de nfl:day: (TTL largo)

# ── Muestras y tope de confianza ──────────────────────────────────────────────
# n = juegos de temporada acumulados por ambos equipos (no hay ventana de
# "últimos N" fija como en MLB — una temporada de NFL son solo 17-18 juegos por
# equipo, así que la temporada completa hasta la fecha ES la ventana reciente).
MIN_SAMPLE      = 4
CONFIDENCE_CAPS = [(4, 70), (8, 80), (15, 88), (30, 93)]
MAX_CONFIDENCE  = 93
MIN_CONFIDENCE_EMIT = 55

# ── Edge mínimo para emitir pick ───────────────────────────────────────────────
# Por debajo de esto el "edge" es ruido de estimación, no señal real — no vale
# la pena diferenciarlo del precio del mercado.
MIN_EDGE = 0.02          # 2 puntos porcentuales de probabilidad
EDGE_CONF_SCALE = 300.0  # edge de 0.08 (8pp) -> +24 sobre el 50 base

# ── Constantes derivadas de datos ──────────────────────────────────────────────
# Medidas sobre la temporada regular 2025 COMPLETA (272 juegos finales, backfill
# de este mismo build — ver scratchpad/derive_constants.py). NO son valores de
# libro de texto sin verificar:
#   HOME_FIELD_ADV = mean(home_score - away_score)              = 2.0699
#   MARGIN_STD     = pstdev(home_score - away_score)             = 14.1512
#   TOTAL_STD      = pstdev(home_score + away_score)              = 13.7887
HOME_FIELD_ADV = 2.07   # puntos — diferencia real de anotación local vs visitante, 2025
MARGIN_STD     = 14.15  # desviación estándar del margen de juego (home - away), 2025
TOTAL_STD      = 13.79  # desviación estándar del total de puntos por juego, 2025

FINAL_STATUS_NAMES = ('STATUS_FINAL', 'STATUS_FULL_TIME')

MARKET_LABELS = {
    'moneyline': lambda m: f"Gana {m['pick_team_name']}",
    'spread':    lambda m: f"Hándicap {m['pick_team_name']} {m['line']:+.1f}",
    'total':     lambda m: f"Total de puntos {'Over' if m['side'] == 'over' else 'Under'} {m['line']}",
}


def _cap_for(n: int):
    """Tope de confianza según la muestra total. None ⇒ rechazar el pick."""
    if n < MIN_SAMPLE:
        return None
    cap = MAX_CONFIDENCE
    for threshold, value in CONFIDENCE_CAPS:
        if n < threshold:
            return value
        cap = value
    return min(cap, MAX_CONFIDENCE)


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _normal_cdf(x: float) -> float:
    return 0.5 * (1 + math.erf(x / math.sqrt(2)))


def devig_two_way(decimal_a, decimal_b):
    """
    Standard no-vig two-way removal:
      p_a = (1/decimal_a) / (1/decimal_a + 1/decimal_b)
    Returns (p_a, p_b), or (None, None) if either price is missing/invalid.
    """
    if not decimal_a or not decimal_b or decimal_a <= 1 or decimal_b <= 1:
        return None, None
    inv_a, inv_b = 1.0 / decimal_a, 1.0 / decimal_b
    total = inv_a + inv_b
    if total <= 0:
        return None, None
    return inv_a / total, inv_b / total


# ══════════════════════════════════════════════════════════════════════════════
#  Normalización de la caché diaria
# ══════════════════════════════════════════════════════════════════════════════

def _side_from_event(competitors: list, home_away: str) -> dict:
    c = next((x for x in competitors if x.get('homeAway') == home_away), {}) or {}
    team = c.get('team') or {}
    score = c.get('score')
    try:
        score = int(score) if score not in (None, '') else None
    except (TypeError, ValueError):
        score = None
    return {
        'id': team.get('id'),
        'name': team.get('displayName'),
        'abbr': team.get('abbreviation'),
        'score': score,
    }


def extract_odds(summary: dict) -> dict | None:
    """
    Picks DraftKings from `pickcenter` when present, else the first bookmaker.
    Returns raw American odds — conversion to decimal happens at analysis time
    via `utils/odds.py:normalize_odds`, never duplicated here.
    """
    pc = summary.get('pickcenter') or []
    if not pc:
        return None
    entry = next((p for p in pc if (p.get('provider') or {}).get('name') == 'DraftKings'), pc[0])
    home = entry.get('homeTeamOdds') or {}
    away = entry.get('awayTeamOdds') or {}
    odds = {
        'provider':          (entry.get('provider') or {}).get('name'),
        'spread_home':       entry.get('spread'),        # home-perspective spread; negative = home favored
        'over_under':        entry.get('overUnder'),
        'over_odds':         entry.get('overOdds'),        # American
        'under_odds':        entry.get('underOdds'),       # American
        'home_ml':           home.get('moneyLine'),        # American
        'away_ml':           away.get('moneyLine'),        # American
        'home_spread_odds':  home.get('spreadOdds'),       # American
        'away_spread_odds':  away.get('spreadOdds'),       # American
    }
    if all(v is None for k, v in odds.items() if k != 'provider'):
        return None
    return odds


def extract_box(summary: dict) -> dict | None:
    """
    Boxscore-lite: total yards, turnovers, first downs per side. Only present
    once the game has started/finished — ESPN serves *PerGame* season-average
    keys for games that haven't kicked off yet, so we key off `totalYards`
    (a per-game-only stat) to avoid storing season averages as if they were
    this game's box score.
    """
    teams = (summary.get('boxscore') or {}).get('teams') or []
    if not teams:
        return None
    out = {}
    for t in teams:
        ha = t.get('homeAway')
        if ha not in ('home', 'away'):
            continue
        stats = {s.get('name'): s.get('displayValue') for s in (t.get('statistics') or [])}
        if 'totalYards' not in stats:
            continue

        def _num(v):
            try:
                return float(v)
            except (TypeError, ValueError):
                return None

        out[ha] = {
            'total_yards': _num(stats.get('totalYards')),
            'turnovers':   _num(stats.get('turnovers')),
            'first_downs': _num(stats.get('firstDowns')),
        }
    return out or None


def compact_game(event: dict, summary: dict | None = None) -> dict:
    """Recorta un evento del scoreboard (+ opcionalmente su summary) a lo que el motor necesita."""
    comp = (event.get('competitions') or [{}])[0]
    status = comp.get('status') or event.get('status') or {}
    stype = status.get('type') or {}
    competitors = comp.get('competitors') or []
    venue = comp.get('venue') or {}

    game = {
        'event_id':    event.get('id'),
        'date':        (event.get('date') or '')[:10],
        'game_date':   event.get('date'),
        'week':        (event.get('week') or {}).get('number'),
        'season_year': (event.get('season') or {}).get('year'),
        'season_type': (event.get('season') or {}).get('type'),
        'state':       stype.get('name'),
        'completed':   bool(stype.get('completed')),
        'period':        status.get('period'),         # cuarto (1-4, 5=OT) — solo UI, el motor no lo usa
        'display_clock': status.get('displayClock'),   # "3:53" — idem
        'venue':       venue.get('fullName'),
        'home':        _side_from_event(competitors, 'home'),
        'away':        _side_from_event(competitors, 'away'),
        'odds':        None,
        'box':         None,
    }
    if summary:
        game['odds'] = extract_odds(summary)
        game['box']  = extract_box(summary)
    return game


def is_final(game: dict) -> bool:
    return (bool(game.get('completed'))
            and game.get('home', {}).get('score') is not None
            and game.get('away', {}).get('score') is not None)


# ══════════════════════════════════════════════════════════════════════════════
#  Contexto derivado de la caché
# ══════════════════════════════════════════════════════════════════════════════

def build_team_context(all_games: list, as_of: str) -> dict:
    """
    Historial por equipo con TODOS los juegos cacheados, SOLO con fecha
    estrictamente anterior a `as_of` (evita fuga de información en backtest).
    No hay ventana de "últimos N" — una temporada de NFL ya es corta (17-18
    juegos), así que lo acumulado hasta la fecha ES la muestra reciente.
    """
    rows = defaultdict(list)
    for g in all_games:
        if not is_final(g) or g['date'] >= as_of:
            continue
        h, a = g['home'], g['away']
        if h.get('id') is None or a.get('id') is None:
            continue
        rows[h['id']].append({'date': g['date'], 'pf': h['score'], 'pa': a['score']})
        rows[a['id']].append({'date': g['date'], 'pf': a['score'], 'pa': h['score']})

    ctx = {}
    for tid, games in rows.items():
        games.sort(key=lambda x: x['date'], reverse=True)
        ctx[tid] = {
            'n':            len(games),
            'ppg_for':      _mean([x['pf'] for x in games]),
            'ppg_against':  _mean([x['pa'] for x in games]),
            'diff':         _mean([x['pf'] - x['pa'] for x in games]),
        }
    return ctx


# ══════════════════════════════════════════════════════════════════════════════
#  Servicio
# ══════════════════════════════════════════════════════════════════════════════

class NFLRadarService:
    def __init__(self, redis_client=None):
        self.r = redis_client
        self.client = NFLApiClient()
        self.local_tz = pytz.timezone('America/Mexico_City')

    # ── calendario liviano (vista Partidos) ──────────────────────────────────

    def schedule_key(self, date: str) -> str:
        return f'nfl:schedule:{date}'

    def get_schedule(self, date: str, force_refresh: bool = False) -> list:
        """
        Calendario crudo del día — equipos, marcador, estado. SIN odds ni
        boxscore (nada de llamadas a `summary`, una sola llamada a
        `scoreboard`). TTL corto para reflejar marcadores en vivo.

        Deliberadamente separado de `get_day`/`nfl:day:`: ese caché es del
        motor de picks (TTL 30 d, pensado para historial inmutable) y no debe
        usarse para una vista que se refresca cada minuto.
        """
        key = self.schedule_key(date)
        if self.r and not force_refresh:
            cached = self.r.get(key)
            if cached:
                return json.loads(cached)

        data = self.client.get_scoreboard(date=date.replace('-', ''))
        events = data.get('events') or []
        games = [compact_game(ev) for ev in events]

        if self.r and games:
            self.r.setex(key, SCHEDULE_TTL, json.dumps(games))
        return games

    # ── caché diaria (motor de picks) ────────────────────────────────────────

    def day_key(self, date: str) -> str:
        return f'nfl:day:{date}'

    def get_day(self, date: str, force_refresh: bool = False) -> list:
        """Schedule compacto de un día específico. Pasado = inmutable ⇒ TTL largo."""
        key = self.day_key(date)
        if self.r and not force_refresh:
            cached = self.r.get(key)
            if cached:
                return json.loads(cached)

        data = self.client.get_scoreboard(date=date.replace('-', ''))
        events = data.get('events') or []
        games = []
        for ev in events:
            eid = ev.get('id')
            time.sleep(REQUEST_SLEEP)
            summary = self.client.get_summary(eid) if eid else {}
            games.append(compact_game(ev, summary))

        if self.r and games:
            self.r.setex(key, DAY_TTL, json.dumps(games))
        return games

    def backfill_week(self, year: int, week: int, seasontype: int = 2,
                      force_refresh: bool = False) -> dict:
        """
        Trae una semana completa (~14-16 juegos, posiblemente repartidos en
        varias fechas de calendario) y la escribe en `nfl:day:{fecha}` una por
        una. Idempotente: si un evento ya está cacheado para su fecha, se salta
        (a menos que `force_refresh=True`, usado para refrescar marcadores de
        juegos recientes que aún no habían terminado).
        """
        data = self.client.get_scoreboard(week=week, seasontype=seasontype, year=year)
        events = data.get('events') or []
        by_date = defaultdict(list)
        for ev in events:
            d = (ev.get('date') or '')[:10]
            by_date[d].append(ev)

        fetched = 0
        for date, evs in by_date.items():
            existing = {}
            if self.r:
                cached = self.r.get(self.day_key(date))
                if cached:
                    existing = {g['event_id']: g for g in json.loads(cached)}
            for ev in evs:
                eid = ev.get('id')
                already = existing.get(eid)
                if already is not None and not force_refresh:
                    continue
                if already is not None and force_refresh and is_final(already):
                    continue  # ya está terminado y cacheado — inmutable, no re-pedir
                time.sleep(REQUEST_SLEEP)
                summary = self.client.get_summary(eid) if eid else {}
                existing[eid] = compact_game(ev, summary)
                fetched += 1
            if self.r and existing:
                self.r.setex(self.day_key(date), DAY_TTL, json.dumps(list(existing.values())))

        return {
            'year': year, 'week': week, 'seasontype': seasontype,
            'dates': sorted(by_date.keys()),
            'events_total': len(events),
            'events_fetched': fetched,
        }

    def load_all_cached(self) -> list:
        """Todos los juegos cacheados en `nfl:day:*` — usado para armar el contexto por equipo."""
        if not self.r:
            return []
        games = []
        for key in self.r.scan_iter(match='nfl:day:*'):
            raw = self.r.get(key)
            if raw:
                games.extend(json.loads(raw))
        return games

    # ── público ───────────────────────────────────────────────────────────────

    def get_suggestions(self, date: str) -> dict:
        """
        Camino en vivo: refresca la caché del día pedido y calcula picks usando
        todo el historial cacheado (`nfl:day:*`) como contexto de equipo.
        """
        today = self.get_day(date, force_refresh=True)
        all_games = self.load_all_cached()
        return self.analyze_slate(today, date, all_games)

    def analyze_slate(self, games: list, date: str, all_games: list) -> dict:
        """
        Seam compartido por producción y backtest: recibe los juegos del día y
        el histórico completo ya cargados, y devuelve el payload de picks. No
        hace red ni Redis — el backtest le pasa listas construidas offline.
        """
        team_ctx = build_team_context(all_games, date)

        results = []
        for g in games:
            analysis = self._analyze_game(g, team_ctx)
            if analysis and analysis['top_picks']:
                results.append(analysis)

        results.sort(key=lambda x: x['top_picks'][0]['confidence'], reverse=True)
        return {
            'date': date,
            'games_analyzed': len(games),
            'suggestions': results,
        }

    # ── por juego ─────────────────────────────────────────────────────────────

    def _analyze_game(self, g, team_ctx):
        home, away = g['home'], g['away']
        ht = team_ctx.get(home.get('id'))
        at = team_ctx.get(away.get('id'))
        odds = g.get('odds')

        markets = {}
        ml = self._analyze_moneyline(home, away, ht, at, odds)
        if ml:
            markets['moneyline'] = ml
        sp = self._analyze_spread(home, away, ht, at, odds)
        if sp:
            markets['spread'] = sp
        tot = self._analyze_total(ht, at, odds)
        if tot:
            markets['total'] = tot

        top_picks = self._build_top_picks(markets)
        if not top_picks:
            return None

        return {
            'event_id':  g['event_id'],
            'date':      g['date'],
            'game_date': g.get('game_date'),
            'week':      g.get('week'),
            'home_team': {'id': home.get('id'), 'name': home.get('name')},
            'away_team': {'id': away.get('id'), 'name': away.get('name')},
            'result':    f"{away.get('score')}-{home.get('score')}" if is_final(g) else None,
            'markets':   markets,
            'top_picks': top_picks,
        }

    # ── mercados ──────────────────────────────────────────────────────────────

    def _analyze_moneyline(self, home, away, ht, at, odds):
        if not (ht and at) or ht['diff'] is None or at['diff'] is None or not odds:
            return None
        n = ht['n'] + at['n']
        cap = _cap_for(n)
        if cap is None:
            return None

        home_ml, away_ml = odds.get('home_ml'), odds.get('away_ml')
        if home_ml is None or away_ml is None:
            return None
        dec_home, dec_away = normalize_odds(home_ml), normalize_odds(away_ml)
        p_home_mkt, p_away_mkt = devig_two_way(dec_home, dec_away)
        if p_home_mkt is None:
            return None

        margin_est = (ht['diff'] - at['diff']) + HOME_FIELD_ADV
        p_home_our = _normal_cdf(margin_est / MARGIN_STD)

        edge_home = p_home_our - p_home_mkt
        if edge_home > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd, pick = 'home', edge_home, p_home_our, p_home_mkt, dec_home, home
        elif -edge_home > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd, pick = 'away', -edge_home, 1 - p_home_our, p_away_mkt, dec_away, away
        else:
            return None

        conf = min(round(50 + edge * EDGE_CONF_SCALE), cap, MAX_CONFIDENCE)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': None, 'confidence': conf,
            'edge': round(edge, 3), 'our_prob': round(our_prob, 3), 'market_prob': round(mkt_prob, 3),
            'pick_team_id': pick.get('id'), 'pick_team_name': pick.get('name'),
            'home_diff': round(ht['diff'], 1), 'away_diff': round(at['diff'], 1),
            'odd': dec_odd,
            'samples': {'home_team': ht['n'], 'away_team': at['n'], 'total': n},
        }

    def _analyze_spread(self, home, away, ht, at, odds):
        if not (ht and at) or ht['diff'] is None or at['diff'] is None or not odds:
            return None
        n = ht['n'] + at['n']
        cap = _cap_for(n)
        if cap is None:
            return None

        spread_home = odds.get('spread_home')
        home_so, away_so = odds.get('home_spread_odds'), odds.get('away_spread_odds')
        if spread_home is None or home_so is None or away_so is None:
            return None
        dec_home, dec_away = normalize_odds(home_so), normalize_odds(away_so)
        p_home_mkt, p_away_mkt = devig_two_way(dec_home, dec_away)
        if p_home_mkt is None:
            return None

        margin_est = (ht['diff'] - at['diff']) + HOME_FIELD_ADV
        # Home covers if actual margin (home - away) > -spread_home.
        p_home_cover_our = _normal_cdf((margin_est + spread_home) / MARGIN_STD)

        edge_home = p_home_cover_our - p_home_mkt
        if edge_home > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd, pick, line = \
                'home', edge_home, p_home_cover_our, p_home_mkt, dec_home, home, spread_home
        elif -edge_home > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd, pick, line = \
                'away', -edge_home, 1 - p_home_cover_our, p_away_mkt, dec_away, away, -spread_home
        else:
            return None

        conf = min(round(50 + edge * EDGE_CONF_SCALE), cap, MAX_CONFIDENCE)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': round(line, 1), 'confidence': conf,
            'edge': round(edge, 3), 'our_prob': round(our_prob, 3), 'market_prob': round(mkt_prob, 3),
            'pick_team_id': pick.get('id'), 'pick_team_name': pick.get('name'),
            'projected_margin': round(margin_est, 1),
            'odd': dec_odd,
            'samples': {'home_team': ht['n'], 'away_team': at['n'], 'total': n},
        }

    def _analyze_total(self, ht, at, odds):
        if not (ht and at) or ht['ppg_for'] is None or at['ppg_for'] is None or not odds:
            return None
        n = ht['n'] + at['n']
        cap = _cap_for(n)
        if cap is None:
            return None

        line = odds.get('over_under')
        over_odds, under_odds = odds.get('over_odds'), odds.get('under_odds')
        if line is None or over_odds is None or under_odds is None:
            return None
        dec_over, dec_under = normalize_odds(over_odds), normalize_odds(under_odds)
        p_over_mkt, p_under_mkt = devig_two_way(dec_over, dec_under)
        if p_over_mkt is None:
            return None

        # Entorno de anotación esperado: ofensiva propia mezclada con la
        # defensiva permitida por el rival.
        exp_home = (ht['ppg_for'] + at['ppg_against']) / 2
        exp_away = (at['ppg_for'] + ht['ppg_against']) / 2
        proj_total = exp_home + exp_away

        p_over_our = _normal_cdf((proj_total - line) / TOTAL_STD)

        edge_over = p_over_our - p_over_mkt
        if edge_over > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd = 'over', edge_over, p_over_our, p_over_mkt, dec_over
        elif -edge_over > MIN_EDGE:
            side, edge, our_prob, mkt_prob, dec_odd = 'under', -edge_over, 1 - p_over_our, p_under_mkt, dec_under
        else:
            return None

        conf = min(round(50 + edge * EDGE_CONF_SCALE), cap, MAX_CONFIDENCE)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': line, 'confidence': conf,
            'edge': round(edge, 3), 'our_prob': round(our_prob, 3), 'market_prob': round(mkt_prob, 3),
            'projected': round(proj_total, 1),
            'odd': dec_odd,
            'samples': {'home_team': ht['n'], 'away_team': at['n'], 'total': n},
        }

    # ── ensamblado ────────────────────────────────────────────────────────────

    @staticmethod
    def _note(market, d):
        edge_pp = round(d['edge'] * 100, 1)
        base = f"Nuestra prob. {round(d['our_prob'] * 100)}% vs mercado {round(d['market_prob'] * 100)}% (edge +{edge_pp}pp)"
        if market == 'moneyline':
            return f"{base} · diff local {d['home_diff']} vs visitante {d['away_diff']}"
        if market == 'spread':
            return f"{base} · margen proyectado {d['projected_margin']}"
        if market == 'total':
            return f"{base} · proyección {d['projected']} pts vs línea {d['line']}"
        return base

    def _build_top_picks(self, markets: dict):
        picks = []
        for market, d in markets.items():
            picks.append({
                'market': market,
                'label': MARKET_LABELS[market](d),
                'note': self._note(market, d),
                'confidence': d['confidence'],
                'side': d['side'],
                'line': d.get('line'),
                'samples': d.get('samples', {}),
                'odd': d.get('odd'),
                'edge': d.get('edge'),
                'our_prob': d.get('our_prob'),
                'market_prob': d.get('market_prob'),
            })
        picks.sort(key=lambda p: -p['confidence'])
        return picks

    # ══════════════════════════════════════════════════════════════════════════
    #  Accuracy — hit rate vs mercado + ROI real (momio decimal real por pick)
    # ══════════════════════════════════════════════════════════════════════════

    def get_accuracy(self, redis_client=None, days: int = 7, min_confidence: int = 70,
                     end_date: str = None) -> dict:
        r = redis_client or self.r
        end = (datetime.strptime(end_date, '%Y-%m-%d').date() if end_date
               else datetime.now(self.local_tz).date())

        cache_key = f'nfl_radar:accuracy:{end}:{days}:{min_confidence}'
        if r:
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)

        dates = [(end - timedelta(days=i)).strftime('%Y-%m-%d')
                 for i in range(1, days + 1)]

        picks, found, missing = [], [], []
        for d in dates:
            raw = r.get(f'nfl_radar:{d}') if r else None
            if not raw:
                missing.append(d)
                continue
            found.append(d)
            for s in json.loads(raw).get('suggestions', []):
                for p in s.get('top_picks', []):
                    if p['confidence'] >= min_confidence:
                        picks.append({**p, 'date': d, 'event_id': s['event_id']})

        results = {}
        for d in found:
            for g in self.get_day(d, force_refresh=False):
                results[g['event_id']] = g

        by_market = defaultdict(lambda: {'wins': 0, 'losses': 0, 'staked': 0.0, 'returned': 0.0})
        wins = losses = unsettled = 0
        staked = returned = 0.0

        for p in picks:
            outcome = self._evaluate_pick(p, results.get(p['event_id']))
            if outcome is None:
                unsettled += 1
                continue
            odd = p.get('odd') or 0
            by_market[p['market']]['staked'] += 1.0
            staked += 1.0
            if outcome == 'win':
                wins += 1
                by_market[p['market']]['wins'] += 1
                by_market[p['market']]['returned'] += odd
                returned += odd
            else:
                losses += 1
                by_market[p['market']]['losses'] += 1

        settled = wins + losses
        result = {
            'days': days, 'min_confidence': min_confidence,
            'dates_analyzed': found, 'dates_missing': missing,
            'total_picks': len(picks), 'settled': settled, 'unsettled': unsettled,
            'wins': wins, 'losses': losses,
            'accuracy': round(wins / settled * 100) if settled else None,
            'roi': round((returned - staked) / staked * 100, 2) if staked else None,
            'by_market': {
                m: {
                    'wins': v['wins'], 'total': v['wins'] + v['losses'],
                    'accuracy': round(v['wins'] / (v['wins'] + v['losses']) * 100) if (v['wins'] + v['losses']) else None,
                    'roi': round((v['returned'] - v['staked']) / v['staked'] * 100, 2) if v['staked'] else None,
                }
                for m, v in by_market.items() if v['wins'] + v['losses']
            },
        }

        if r:
            r.setex(cache_key, ACCURACY_TTL, json.dumps(result))
        return result

    def _evaluate_pick(self, pick, game):
        """'win' | 'loss' | None (juego no terminado, falta dato, o push)."""
        if game is None or not is_final(game):
            return None
        market, side, line = pick['market'], pick['side'], pick.get('line')
        hs, as_ = game['home']['score'], game['away']['score']

        if market == 'moneyline':
            if hs == as_:
                return None
            actual_side = 'home' if hs > as_ else 'away'
            return 'win' if actual_side == side else 'loss'

        if market == 'spread':
            if line is None:
                return None
            margin = (hs - as_) if side == 'home' else (as_ - hs)
            if margin == -line:
                return None  # push
            return 'win' if margin > -line else 'loss'

        if market == 'total':
            if line is None:
                return None
            total = hs + as_
            if total == line:
                return None  # push
            return 'win' if (total > line if side == 'over' else total < line) else 'loss'

        return None
