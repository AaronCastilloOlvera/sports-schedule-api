"""
MLB Radar — motor de picks de béisbol (MLB / LMB).

Redis-only por diseño: statsapi.mlb.com es gratuita, sin autenticación y sin
cuota práctica, así que re-consultar historia no cuesta nada. No hay tablas,
modelos ni migraciones asociadas a este módulo.

Claves Redis que este servicio posee (nadie más escribe en ellas):

  mlb:day:{league}:{YYYY-MM-DD}        30 d  schedule + linescore recortado del día
  mlb:gamelog:{league}:{pid}:{season}  12 h  splits crudos del game log del pitcher
  mlb:pitcher:{league}:{pid}:{date}     2 d  perfil derivado (últimos 10 + vs rival)
  mlb:venue_tz:{venue_id}             180 d  IANA tz id del estadio (no cambia)
  mlb_radar:{league}:{YYYY-MM-DD}      30 d  picks calculados del día
  mlb_radar:accuracy:{league}:{...}     1 h  respuesta de get_accuracy

Todos los picks salen con la forma del feed de fútbol:
  {market, label, note, confidence, side, line, samples, odd}
`odd` siempre es None — no existe fuente de momios para MLB.
"""
import json
from collections import defaultdict
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo

import pytz

from services.mlb_api_client import MLBApiClient, LEAGUES, POSTSEASON_GAME_TYPES

# ── TTLs ──────────────────────────────────────────────────────────────────────
DAY_TTL      = 30 * 24 * 3600   # 30 d — un día pasado es inmutable
GAMELOG_TTL  = 12 * 3600        # 12 h — aparece una línea nueva cada ~5 días
PITCHER_TTL  = 2 * 24 * 3600    # 2 d  — perfil derivado, se recalcula cada noche
PICKS_TTL    = 30 * 24 * 3600   # 30 d — necesario para medir accuracy después
ACCURACY_TTL = 3600             # 1 h
VENUE_TZ_TTL = 180 * 24 * 3600  # 180 d — la tz de un estadio no cambia

# ── Líneas fijas de casa de apuestas ──────────────────────────────────────────
# NUNCA se eligen a partir de nuestra propia proyección: se eligen por cercanía
# a la expectativa neutral (promedio de liga / baseline encogido). De lo contrario
# el motor siempre cae del lado cómodo y el hit rate resultante es ficticio.
TOTAL_LINES = [7.5, 8.5, 9.5]
# Hits combinados del JUEGO (ambos equipos) — no existe prop de hits por
# pitcher en el casino del usuario. Promedio real medido sobre 2,309 juegos: 16.41.
TEAM_HITS_LINES = [14.5, 15.5, 16.5, 17.5, 18.5]

# ── Muestras y tope de confianza ──────────────────────────────────────────────
RECENT_STARTS   = 10   # aperturas recientes por pitcher
RECENT_TEAM     = 20   # juegos recientes por equipo
RECENT_TEAM_1ST = 25   # juegos para la tasa de anotar en el 1er inning
MIN_SAMPLE      = 4    # bajo esto se rechaza el pick por completo

# Tope duro de confianza por tamaño de muestra total que respalda el pick.
# n < 4 → se rechaza. Nunca se emite 100.
CONFIDENCE_CAPS = [(4, 70), (8, 80), (15, 88), (30, 93)]
MAX_CONFIDENCE  = 93
MIN_CONFIDENCE_EMIT = 60  # backtest: la banda 56-59 rinde 51% (ruido puro)

# ── Park factor ───────────────────────────────────────────────────────────────
PARK_MIN_GAMES = 20    # menos de esto → factor 1.0
PARK_CLAMP     = (0.85, 1.15)

# ── Día de la semana / franja horaria ───────────────────────────────────────────
# Un solo multiplicador por día específico (0=Lun..6=Dom) — igual que
# `_compute_dow_multipliers()` de fútbol: no hay categoría aparte de
# entre-semana/fin-de-semana, el efecto de fin de semana ya queda capturado en
# los multiplicadores de sábado/domingo. Franja horaria calculada en hora LOCAL
# del estadio (vía `MLBApiClient.get_venue_timezone`) para no mezclar las 4
# zonas horarias de EEUU en un solo corte UTC.
DOW_MIN_SAMPLE = 30
TIME_BUCKETS = ('day', 'afternoon', 'evening')  # <15:00 / 15:00-18:00 / 18:00+ local
DEFAULT_VENUE_TZ = 'America/New_York'     # fallback si la API no resuelve la tz

# ── Pesos del modelo ──────────────────────────────────────────────────────────
ML_W_TEAM       = 0.80   # diferencial de fuerza de equipo
ML_W_SEASON     = 1.00   # peso del récord de temporada dentro de esa fuerza
ML_W_PITCHER    = 0.30   # diferencial de carreras permitidas del abridor
ML_W_VS_OPP_MAX = 0.10   # historial del abridor vs ese rival (muestra chica)
ML_RA9_SCALE    = 6.0    # 6 carreras/9 de diferencia satura el término
ML_PROB_CLAMP   = (0.25, 0.78)

NRFI_SHRINK_K   = 6.0    # pseudo-observaciones hacia la tasa de liga

TOTAL_W_OFFENSE = 0.50
TOTAL_W_PITCHER = 0.55
TOTAL_SP_IP_FRAC = 0.60  # parte del juego que cubre el abridor
TOTAL_MARGIN_SCALE = 14.0
TOTAL_MARGIN_CAP   = 22.0

# Hits totales del juego — mismos pesos que TOTAL (carreras), otra magnitud.
TEAM_HITS_W_OFFENSE   = 0.50
TEAM_HITS_W_PITCHER   = 0.55
TEAM_HITS_SP_IP_FRAC  = 0.60
TEAM_HITS_MARGIN_SCALE = 10.0
TEAM_HITS_MARGIN_CAP   = 22.0

# Defaults de liga usados solo si la caché de días está vacía
FALLBACK_LEAGUE = {
    'total_runs': 8.9, 'team_runs': 4.45, 'team_hits': 8.2,
    'home_win_rate': 0.53, 'first_inning_score_rate': 0.27,
    'sp_ra9': 4.4, 'sp_hits': 5.0,
}

FINAL_STATES = ('Final', 'Game Over', 'Completed Early')

MARKET_LABELS = {
    'moneyline': lambda m: f"Gana {m['pick_team_name']}",
    'nrfi':      lambda m: f"1er inning sin carreras: {'Sí' if m['side'] == 'yes' else 'No'}",
    'total':     lambda m: f"Carreras totales {'Over' if m['side'] == 'over' else 'Under'} {m['line']}",
    'hits':      lambda m: f"Hits totales {'Over' if m['side'] == 'over' else 'Under'} {m['line']}",
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


def _clamp(v, lo, hi):
    return max(lo, min(hi, v))


def _mean(vals):
    vals = [v for v in vals if v is not None]
    return sum(vals) / len(vals) if vals else None


def _outs_of(stat: dict) -> int:
    """Outs registrados en la apertura.

    `stat['outs']` viene directo. Fallback: `inningsPitched` es un STRING tipo
    "5.2" donde el decimal son TERCIOS (5 y 2/3), no décimas.
    """
    if stat.get('outs') is not None:
        try:
            return int(stat['outs'])
        except (TypeError, ValueError):
            pass
    ip = stat.get('inningsPitched')
    if not ip:
        return 0
    try:
        whole, _, frac = str(ip).partition('.')
        return int(whole or 0) * 3 + int(frac or 0)
    except ValueError:
        return 0


# ══════════════════════════════════════════════════════════════════════════════
#  Normalización de la caché diaria
# ══════════════════════════════════════════════════════════════════════════════

def compact_game(g: dict) -> dict:
    """Recorta un juego del schedule a lo único que el motor necesita."""
    teams = g.get('teams') or {}
    ls    = g.get('linescore') or {}
    innings = ls.get('innings') or []
    first   = innings[0] if innings else {}
    ls_teams = ls.get('teams') or {}

    def side(key):
        t  = teams.get(key) or {}
        tm = t.get('team') or {}
        pp = t.get('probablePitcher') or {}
        rec = t.get('leagueRecord') or {}
        lst = ls_teams.get(key) or {}
        return {
            'id': tm.get('id'),
            'name': tm.get('name'),
            'score': t.get('score'),
            'hits': lst.get('hits'),
            'wins': rec.get('wins'),
            'losses': rec.get('losses'),
            'pitcher_id': pp.get('id'),
            'pitcher_name': pp.get('fullName'),
            'first_inning_runs': (first.get(key) or {}).get('runs'),
        }

    venue = g.get('venue') or {}
    return {
        'gamePk': g.get('gamePk'),
        'date': g.get('officialDate') or (g.get('gameDate') or '')[:10],
        'gameDate': g.get('gameDate'),
        'gameType': g.get('gameType'),
        'state': (g.get('status') or {}).get('detailedState'),
        'venue_id': venue.get('id'),
        'venue_name': venue.get('name'),
        'innings_played': len(innings),
        'home': side('home'),
        'away': side('away'),
    }


def is_final(game: dict) -> bool:
    return game.get('state') in FINAL_STATES and game['home'].get('score') is not None


# ══════════════════════════════════════════════════════════════════════════════
#  Contexto derivado de la caché diaria
# ══════════════════════════════════════════════════════════════════════════════

def build_game_index(day_games: list) -> dict:
    """{gamePk: juego compacto} sobre toda la ventana cacheada."""
    return {g['gamePk']: g for g in day_games if g.get('gamePk')}


def compute_park_factors(day_games: list) -> dict:
    """
    Carreras totales promedio por estadio, normalizadas contra el promedio de
    liga. Se calcula UNA vez por corrida del pipeline, nunca por juego.
    Espejo de `_compute_dow_multipliers()` del motor de fútbol.
    """
    by_venue = defaultdict(list)
    all_totals = []
    for g in day_games:
        if not is_final(g) or not g.get('venue_id'):
            continue
        total = (g['home'].get('score') or 0) + (g['away'].get('score') or 0)
        by_venue[g['venue_id']].append(total)
        all_totals.append(total)

    if not all_totals:
        return {}
    league_avg = sum(all_totals) / len(all_totals)
    if league_avg <= 0:
        return {}

    factors = {}
    for venue_id, totals in by_venue.items():
        if len(totals) < PARK_MIN_GAMES:
            factors[venue_id] = 1.0
            continue
        factors[venue_id] = round(
            _clamp((sum(totals) / len(totals)) / league_avg, *PARK_CLAMP), 3
        )
    return factors


def local_dow_and_bucket(game_date_utc: str | None, venue_id, venue_tz: dict) -> tuple:
    """
    (día 0=Lun..6=Dom, franja 'day'/'afternoon'/'evening') del juego en hora
    LOCAL del estadio. Se resuelve en local (no UTC) porque un juego nocturno
    en la costa oeste puede caer en el día siguiente en UTC.
    """
    if not game_date_utc:
        return None, None
    try:
        dt_utc = datetime.fromisoformat(game_date_utc.replace('Z', '+00:00'))
    except ValueError:
        return None, None
    tz_name = venue_tz.get(venue_id) or DEFAULT_VENUE_TZ
    try:
        local = dt_utc.astimezone(ZoneInfo(tz_name))
    except Exception:
        local = dt_utc.astimezone(ZoneInfo(DEFAULT_VENUE_TZ))
    hour = local.hour
    bucket = 'day' if hour < 15 else ('afternoon' if hour < 18 else 'evening')
    return local.weekday(), bucket


def compute_day_time_multipliers(day_games: list, venue_tz: dict) -> tuple:
    """
    Multiplicadores por día de la semana y por franja horaria, ambos en hora
    LOCAL del estadio. Se calcula UNA vez por corrida del pipeline, nunca por
    juego. Espejo de `compute_park_factors()`: ratio vs. promedio global,
    factor 1.0 si la muestra es menor a DOW_MIN_SAMPLE.

    Devuelve (dow_mults, time_mults), cada uno {clave: {'runs': x, 'hits': x}}.
    """
    dow_runs, dow_hits = defaultdict(list), defaultdict(list)
    time_runs, time_hits = defaultdict(list), defaultdict(list)
    all_runs, all_hits = [], []

    for g in day_games:
        if not is_final(g):
            continue
        dow, bucket = local_dow_and_bucket(g.get('gameDate'), g.get('venue_id'), venue_tz)
        if dow is None:
            continue

        runs = (g['home'].get('score') or 0) + (g['away'].get('score') or 0)
        all_runs.append(runs)
        dow_runs[dow].append(runs)
        time_runs[bucket].append(runs)

        h_hits, a_hits = g['home'].get('hits'), g['away'].get('hits')
        if h_hits is not None and a_hits is not None:
            hits = h_hits + a_hits
            all_hits.append(hits)
            dow_hits[dow].append(hits)
            time_hits[bucket].append(hits)

    def ratios(groups: dict, all_vals: list) -> dict:
        if not all_vals:
            return {}
        avg = sum(all_vals) / len(all_vals)
        if avg <= 0:
            return {}
        out = {}
        for key, vals in groups.items():
            if len(vals) >= DOW_MIN_SAMPLE:
                out[key] = round((sum(vals) / len(vals)) / avg, 3)
        return out

    runs_by_dow, hits_by_dow = ratios(dow_runs, all_runs), ratios(dow_hits, all_hits)
    runs_by_time, hits_by_time = ratios(time_runs, all_runs), ratios(time_hits, all_hits)

    dow_mults = {d: {'runs': runs_by_dow.get(d, 1.0), 'hits': hits_by_dow.get(d, 1.0)}
                for d in range(7)}
    time_mults = {b: {'runs': runs_by_time.get(b, 1.0), 'hits': hits_by_time.get(b, 1.0)}
                 for b in TIME_BUCKETS}
    return dow_mults, time_mults


def compute_league_context(day_games: list) -> dict:
    """Constantes de liga (ofensiva, ventaja local, 1er inning) desde la caché."""
    totals, team_runs, team_hits, home_wins, n_games = [], [], [], 0, 0
    first_half_scored, first_half_n = 0, 0

    for g in day_games:
        if not is_final(g):
            continue
        hs, as_ = g['home'].get('score') or 0, g['away'].get('score') or 0
        totals.append(hs + as_)
        team_runs.extend([hs, as_])
        for s in ('home', 'away'):
            if g[s].get('hits') is not None:
                team_hits.append(g[s]['hits'])
            fi = g[s].get('first_inning_runs')
            if fi is not None:
                first_half_n += 1
                if fi > 0:
                    first_half_scored += 1
        n_games += 1
        if hs > as_:
            home_wins += 1

    ctx = dict(FALLBACK_LEAGUE)
    if totals:
        ctx['total_runs'] = sum(totals) / len(totals)
        ctx['team_runs']  = sum(team_runs) / len(team_runs)
    if team_hits:
        ctx['team_hits'] = sum(team_hits) / len(team_hits)
    if n_games:
        ctx['home_win_rate'] = home_wins / n_games
    if first_half_n:
        ctx['first_inning_score_rate'] = first_half_scored / first_half_n
    ctx['games'] = n_games
    return ctx


def build_team_context(day_games: list, as_of: str) -> dict:
    """
    Historial por equipo a partir de la caché diaria, SOLO con juegos
    estrictamente anteriores a `as_of` (evita fuga de información en backtest).
    """
    rows = defaultdict(list)
    for g in day_games:
        if not is_final(g) or g['date'] >= as_of:
            continue
        for s, o in (('home', 'away'), ('away', 'home')):
            tid = g[s].get('id')
            if tid is None:
                continue
            h_hits, a_hits = g['home'].get('hits'), g['away'].get('hits')
            rows[tid].append({
                'date': g['date'],
                'is_home': s == 'home',
                'runs_for': g[s].get('score') or 0,
                'runs_against': g[o].get('score') or 0,
                'hits_for': g[s].get('hits'),
                'total': (g['home'].get('score') or 0) + (g['away'].get('score') or 0),
                'hits_total': (h_hits + a_hits) if h_hits is not None and a_hits is not None else None,
                'won': (g[s].get('score') or 0) > (g[o].get('score') or 0),
                'scored_first_inning': (g[s].get('first_inning_runs') or 0) > 0
                                       if g[s].get('first_inning_runs') is not None else None,
            })

    ctx = {}
    for tid, games in rows.items():
        games.sort(key=lambda x: x['date'], reverse=True)
        recent = games[:RECENT_TEAM]
        first  = [g for g in games[:RECENT_TEAM_1ST] if g['scored_first_inning'] is not None]
        ctx[tid] = {
            'n': len(recent),
            'season_n': len(games),
            # Récord de temporada acumulado: menos ruidoso que la forma corta y,
            # en backtest, el único término del moneyline que bate al baseline.
            'season_win_rate': _mean([1.0 if g['won'] else 0.0 for g in games]),
            'win_rate': _mean([1.0 if g['won'] else 0.0 for g in recent]),
            'runs_per_game': _mean([g['runs_for'] for g in recent]),
            'runs_allowed_per_game': _mean([g['runs_against'] for g in recent]),
            'hits_per_game': _mean([g['hits_for'] for g in recent]),
            'totals': [g['total'] for g in recent],
            'hits_totals': [g['hits_total'] for g in recent if g['hits_total'] is not None],
            'first_inning_rate': _mean([1.0 if g['scored_first_inning'] else 0.0 for g in first]),
            'first_inning_n': len(first),
        }
    return ctx


def build_pitcher_profile(splits: list, as_of: str, opponent_id: int | None,
                          game_index: dict) -> dict | None:
    """
    Convierte el game log crudo en el perfil que consume el motor.

    - Solo aperturas (`gamesStarted == 1`) con fecha ESTRICTAMENTE anterior a
      `as_of`. Una sola llamada al game log cubre tanto "últimas 10 aperturas"
      como "historial vs este rival": son dos filtros sobre la misma respuesta.
    - De-dupe por `game.gamePk` (la API emite un split 'P' agregado duplicado
      por juego de postemporada).
    - El primer inning NO está en el game log: se obtiene cruzando el gamePk
      contra la caché diaria.
    """
    seen, starts = set(), []
    for sp in splits:
        stat = sp.get('stat') or {}
        if not stat.get('gamesStarted'):
            continue
        pk = (sp.get('game') or {}).get('gamePk')
        if pk in seen:
            continue
        seen.add(pk)
        date = sp.get('date')
        if not date or date >= as_of:
            continue
        outs = _outs_of(stat)
        if outs <= 0:
            continue
        cached = game_index.get(pk)
        first_allowed = None
        if cached:
            # El abridor local lanza la parte alta del 1º → carreras del visitante.
            opp_side = 'away' if sp.get('isHome') else 'home'
            fi = cached[opp_side].get('first_inning_runs')
            if fi is not None:
                first_allowed = fi > 0
        starts.append({
            'date': date,
            'gamePk': pk,
            'is_home': bool(sp.get('isHome')),
            'opponent_id': (sp.get('opponent') or {}).get('id'),
            'outs': outs,
            'ip': outs / 3.0,
            'hits': stat.get('hits') or 0,
            'runs': stat.get('runs') or 0,
            'earned_runs': stat.get('earnedRuns') or 0,
            'strike_outs': stat.get('strikeOuts') or 0,
            'won': bool(sp.get('isWin')),
            'allowed_first_inning': first_allowed,
        })

    if not starts:
        return None
    starts.sort(key=lambda s: s['date'], reverse=True)
    recent = starts[:RECENT_STARTS]
    vs_opp = [s for s in starts if opponent_id and s['opponent_id'] == opponent_id]

    def agg(rows):
        if not rows:
            return None
        outs = sum(r['outs'] for r in rows)
        ip   = outs / 3.0 or 1e-9
        firsts = [r for r in rows if r['allowed_first_inning'] is not None]
        return {
            'n': len(rows),
            'ip_per_start': round(ip / len(rows), 2),
            'hits_per_start': round(sum(r['hits'] for r in rows) / len(rows), 2),
            'hits_per_9': round(sum(r['hits'] for r in rows) * 9 / ip, 2),
            'runs_per_9': round(sum(r['runs'] for r in rows) * 9 / ip, 2),
            'era': round(sum(r['earned_runs'] for r in rows) * 9 / ip, 2),
            'win_rate': round(sum(1 for r in rows if r['won']) / len(rows), 3),
            'hits_list': [r['hits'] for r in rows],
            'first_inning_clean_n': len(firsts),
            'first_inning_allowed_rate': (
                round(sum(1 for r in firsts if r['allowed_first_inning']) / len(firsts), 3)
                if firsts else None
            ),
        }

    home_recent = [s for s in recent if s['is_home']]
    away_recent = [s for s in recent if not s['is_home']]

    return {
        'recent': agg(recent),
        'home': agg(home_recent),
        'away': agg(away_recent),
        'vs_opponent': agg(vs_opp),
        'total_starts': len(starts),
    }


# ══════════════════════════════════════════════════════════════════════════════
#  Servicio
# ══════════════════════════════════════════════════════════════════════════════

class MLBRadarService:
    def __init__(self, redis_client=None, league: str = 'mlb'):
        self.r = redis_client
        self.league = league if league in LEAGUES else 'mlb'
        self.client = MLBApiClient()
        self.local_tz = pytz.timezone('America/Mexico_City')

    # ── caché diaria ──────────────────────────────────────────────────────────

    def day_key(self, date: str) -> str:
        return f'mlb:day:{self.league}:{date}'

    def get_day(self, date: str, force_refresh: bool = False) -> list:
        """Schedule compacto del día. Pasado = inmutable ⇒ TTL largo."""
        key = self.day_key(date)
        if self.r and not force_refresh:
            cached = self.r.get(key)
            if cached:
                return json.loads(cached)
        games = [compact_game(g) for g in self.client.get_schedule(date, self.league)]
        if self.r and games:
            self.r.setex(key, DAY_TTL, json.dumps(games))
        return games

    def load_window(self, end_date: str, days: int, refresh_last: int = 0) -> list:
        """Une la caché de `days` días terminando (inclusive) en `end_date`."""
        end = datetime.strptime(end_date, '%Y-%m-%d').date()
        out = []
        for i in range(days):
            d = (end - timedelta(days=i)).strftime('%Y-%m-%d')
            out.extend(self.get_day(d, force_refresh=i < refresh_last))
        return out

    # ── game logs ─────────────────────────────────────────────────────────────

    def get_game_log(self, pitcher_id: int, season: int) -> list:
        key = f'mlb:gamelog:{self.league}:{pitcher_id}:{season}'
        if self.r:
            cached = self.r.get(key)
            if cached:
                return json.loads(cached)
        game_types = POSTSEASON_GAME_TYPES if self.league == 'lmb' else None
        splits = self.client.get_person_game_log(pitcher_id, self.league, [season], game_types)
        if self.r and splits:
            self.r.setex(key, GAMELOG_TTL, json.dumps(splits))
        return splits

    # ── zona horaria de estadios ──────────────────────────────────────────────

    def get_venue_tz(self, venue_id: int) -> str:
        if not venue_id:
            return DEFAULT_VENUE_TZ
        key = f'mlb:venue_tz:{venue_id}'
        if self.r:
            cached = self.r.get(key)
            if cached:
                return cached
        tz = self.client.get_venue_timezone(venue_id) or DEFAULT_VENUE_TZ
        if self.r:
            self.r.setex(key, VENUE_TZ_TTL, tz)
        return tz

    def build_venue_tz_lookup(self, games: list) -> dict:
        """{venue_id: tz IANA} de todos los estadios distintos en `games`."""
        venue_ids = {g.get('venue_id') for g in games if g.get('venue_id')}
        return {vid: self.get_venue_tz(vid) for vid in venue_ids}

    # ── público ───────────────────────────────────────────────────────────────

    def get_suggestions(self, date: str, history_days: int = 60,
                        refresh_last: int = 3) -> dict:
        """
        Camino en vivo: refresca la caché del día, carga la ventana histórica,
        arma perfiles de los abridores anunciados y calcula picks.
        """
        today = self.get_day(date, force_refresh=True)
        window = self.load_window(
            (datetime.strptime(date, '%Y-%m-%d').date() - timedelta(days=1)).strftime('%Y-%m-%d'),
            history_days, refresh_last=refresh_last,
        )
        season = int(date[:4])

        profiles = {}
        game_index = build_game_index(window)
        for g in today:
            for s in ('home', 'away'):
                pid = g[s].get('pitcher_id')
                opp = g['away' if s == 'home' else 'home'].get('id')
                if not pid:
                    continue
                pkey = f'mlb:pitcher:{self.league}:{pid}:{date}'
                if self.r:
                    cached = self.r.get(pkey)
                    if cached:
                        profiles[(pid, opp)] = json.loads(cached)
                        continue
                prof = build_pitcher_profile(self.get_game_log(pid, season), date, opp, game_index)
                profiles[(pid, opp)] = prof
                if self.r and prof:
                    self.r.setex(pkey, PITCHER_TTL, json.dumps(prof))

        venue_tz = self.build_venue_tz_lookup(window + today)
        return self.analyze_slate(today, date, window, profiles, venue_tz)

    def analyze_slate(self, games: list, date: str, window: list, profiles: dict,
                      venue_tz: dict) -> dict:
        """
        Seam compartido por producción y backtest: recibe el contexto ya armado
        y devuelve el payload de picks. No hace red ni Redis — `venue_tz` debe
        venir ya resuelto (por eso `get_suggestions` lo arma antes de llamar aquí).
        """
        league_ctx = compute_league_context(window)
        park       = compute_park_factors(window)
        team_ctx   = build_team_context(window, date)
        dow_mults, time_mults = compute_day_time_multipliers(window, venue_tz)

        # Constantes de abridor derivadas del propio slate (solo datos previos).
        sp_ra9  = [p['recent']['runs_per_9']     for p in profiles.values() if p and p.get('recent')]
        sp_hits = [p['recent']['hits_per_start'] for p in profiles.values() if p and p.get('recent')]
        league_ctx['sp_ra9']  = _mean(sp_ra9)  or FALLBACK_LEAGUE['sp_ra9']
        league_ctx['sp_hits'] = _mean(sp_hits) or FALLBACK_LEAGUE['sp_hits']

        results = []
        for g in games:
            analysis = self._analyze_game(g, profiles, team_ctx, league_ctx, park,
                                          dow_mults, time_mults, venue_tz)
            if analysis and analysis['top_picks']:
                results.append(analysis)

        results.sort(key=lambda x: x['top_picks'][0]['confidence'], reverse=True)
        return {
            'date': date,
            'league': self.league,
            'games_analyzed': len(games),
            'league_context': {k: (round(v, 3) if isinstance(v, float) else v)
                               for k, v in league_ctx.items()},
            'suggestions': results,
        }

    # ── por juego ─────────────────────────────────────────────────────────────

    def _analyze_game(self, g, profiles, team_ctx, league_ctx, park,
                      dow_mults, time_mults, venue_tz):
        home, away = g['home'], g['away']
        hp = profiles.get((home.get('pitcher_id'), away.get('id')))
        ap = profiles.get((away.get('pitcher_id'), home.get('id')))
        ht = team_ctx.get(home.get('id'))
        at = team_ctx.get(away.get('id'))
        pf = park.get(g.get('venue_id'), 1.0)

        dow, bucket = local_dow_and_bucket(g.get('gameDate'), g.get('venue_id'), venue_tz)
        dow_mult  = dow_mults.get(dow, {}) if dow is not None else {}
        time_mult = time_mults.get(bucket, {}) if bucket else {}
        runs_mult = dow_mult.get('runs', 1.0) * time_mult.get('runs', 1.0)
        hits_mult = dow_mult.get('hits', 1.0) * time_mult.get('hits', 1.0)

        markets = {}
        ml = self._analyze_moneyline(home, away, hp, ap, ht, at, league_ctx)
        if ml:
            markets['moneyline'] = ml
        nrfi = self._analyze_nrfi(hp, ap, ht, at, league_ctx)
        if nrfi:
            markets['nrfi'] = nrfi
        total = self._analyze_total(hp, ap, ht, at, league_ctx, pf, runs_mult)
        if total:
            markets['total'] = total
        hits = self._analyze_team_hits(hp, ap, ht, at, league_ctx, pf, hits_mult)
        if hits:
            markets['hits'] = hits

        top_picks = self._build_top_picks(markets)
        if not top_picks:
            return None

        return {
            'game_pk': g['gamePk'],
            'date': g['date'],
            'game_date': g.get('gameDate'),
            'venue': {'id': g.get('venue_id'), 'name': g.get('venue_name'), 'park_factor': pf},
            'home_team': {'id': home.get('id'), 'name': home.get('name'),
                          'pitcher': {'id': home.get('pitcher_id'), 'name': home.get('pitcher_name')}},
            'away_team': {'id': away.get('id'), 'name': away.get('name'),
                          'pitcher': {'id': away.get('pitcher_id'), 'name': away.get('pitcher_name')}},
            'result': (f"{away.get('score')}-{home.get('score')}"
                       if is_final(g) else None),
            'markets': markets,
            'top_picks': top_picks,
        }

    # ── mercados ──────────────────────────────────────────────────────────────

    def _analyze_moneyline(self, home, away, hp, ap, ht, at, lg):
        if not (ht and at) or ht['win_rate'] is None or at['win_rate'] is None:
            return None

        n = ht['n'] + at['n']
        # 70% récord de temporada / 30% forma reciente — la forma corta sola es
        # ruido y hace que el modelo pierda contra "siempre el de mejor récord".
        h_str = ML_W_SEASON * (ht.get('season_win_rate') or ht['win_rate']) +                 (1 - ML_W_SEASON) * ht['win_rate']
        a_str = ML_W_SEASON * (at.get('season_win_rate') or at['win_rate']) +                 (1 - ML_W_SEASON) * at['win_rate']
        team_edge = h_str - a_str

        pitch_edge, sp_n = 0.0, 0
        if hp and ap and hp.get('recent') and ap.get('recent'):
            pitch_edge = _clamp(
                (ap['recent']['runs_per_9'] - hp['recent']['runs_per_9']) / ML_RA9_SCALE, -1, 1)
            sp_n = hp['recent']['n'] + ap['recent']['n']
            n += sp_n

        vs_edge, vs_n, vs_w = 0.0, 0, 0.0
        h_vs = (hp or {}).get('vs_opponent')
        a_vs = (ap or {}).get('vs_opponent')
        if h_vs or a_vs:
            hv = h_vs['win_rate'] if h_vs else lg['home_win_rate']
            av = a_vs['win_rate'] if a_vs else (1 - lg['home_win_rate'])
            vs_n = (h_vs['n'] if h_vs else 0) + (a_vs['n'] if a_vs else 0)
            vs_w = min(ML_W_VS_OPP_MAX, vs_n / 4.0 * ML_W_VS_OPP_MAX)
            vs_edge = hv - av

        p_home = _clamp(
            lg['home_win_rate'] + ML_W_TEAM * team_edge + ML_W_PITCHER * pitch_edge + vs_w * vs_edge,
            *ML_PROB_CLAMP)

        side = 'home' if p_home >= 0.5 else 'away'
        raw  = round(max(p_home, 1 - p_home) * 100)
        cap  = _cap_for(n)
        if cap is None:
            return None
        conf = min(raw, cap)
        if conf < MIN_CONFIDENCE_EMIT:
            return None

        pick = home if side == 'home' else away
        return {
            'side': side, 'line': None, 'confidence': conf,
            'p_home': round(p_home, 3),
            'pick_team_id': pick.get('id'), 'pick_team_name': pick.get('name'),
            'home_form': round(ht['win_rate'], 3), 'away_form': round(at['win_rate'], 3),
            'home_sp_ra9': hp['recent']['runs_per_9'] if hp and hp.get('recent') else None,
            'away_sp_ra9': ap['recent']['runs_per_9'] if ap and ap.get('recent') else None,
            'samples': {'home_team': ht['n'], 'away_team': at['n'],
                        'starters': sp_n, 'vs_opponent': vs_n, 'total': n},
        }

    def _analyze_nrfi(self, hp, ap, ht, at, lg):
        base = lg['first_inning_score_rate']

        def half(sp_prof, bat_team):
            """Probabilidad de que el equipo bateador anote en el 1º."""
            parts, n = [], 0
            sp = (sp_prof or {}).get('recent') or {}
            if sp.get('first_inning_allowed_rate') is not None and sp['first_inning_clean_n'] >= 3:
                k = NRFI_SHRINK_K
                cnt = sp['first_inning_allowed_rate'] * sp['first_inning_clean_n']
                parts.append(((cnt + k * base) / (sp['first_inning_clean_n'] + k), 0.5))
                n += sp['first_inning_clean_n']
            if bat_team and bat_team.get('first_inning_rate') is not None and bat_team['first_inning_n'] >= 5:
                k = NRFI_SHRINK_K
                cnt = bat_team['first_inning_rate'] * bat_team['first_inning_n']
                parts.append(((cnt + k * base) / (bat_team['first_inning_n'] + k), 0.5))
                n += bat_team['first_inning_n']
            if not parts:
                return None, 0
            tw = sum(w for _, w in parts)
            return sum(p * w / tw for p, w in parts), n

        p_home_scores, n1 = half(ap, ht)   # abridor visitante vs bateo local
        p_away_scores, n2 = half(hp, at)
        if p_home_scores is None or p_away_scores is None:
            return None

        p_nrfi = (1 - p_home_scores) * (1 - p_away_scores)
        side = 'yes' if p_nrfi >= 0.5 else 'no'
        raw  = round(max(p_nrfi, 1 - p_nrfi) * 100)
        cap  = _cap_for(n1 + n2)
        if cap is None:
            return None
        conf = min(raw, cap)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': None, 'confidence': conf,
            'p_nrfi': round(p_nrfi, 3),
            'p_home_scores': round(p_home_scores, 3),
            'p_away_scores': round(p_away_scores, 3),
            'samples': {'first_inning_home_side': n1, 'first_inning_away_side': n2,
                        'total': n1 + n2},
        }

    def _analyze_total(self, hp, ap, ht, at, lg, park_factor, day_time_mult=1.0):
        if not (ht and at) or ht['runs_per_game'] is None or at['runs_per_game'] is None:
            return None

        # ── Línea: la MÁS CERCANA a la expectativa neutral (promedio de liga ×
        # park factor × día/hora). NO se elige desde nuestra proyección — hacerlo
        # garantiza caer siempre del lado cómodo y produce un hit rate ficticio.
        neutral = lg['total_runs'] * park_factor * day_time_mult
        line = min(TOTAL_LINES, key=lambda l: abs(l - neutral))

        # ── Proyección como DESVIACIÓN respecto a la neutral: centrada en cero
        # por construcción, así el reparto over/under sale ~50/50 de forma natural.
        lg_team = lg['team_runs']
        off_dev = (ht['runs_per_game'] - lg_team) + (at['runs_per_game'] - lg_team)

        sp_dev, sp_n = 0.0, 0
        if hp and hp.get('recent'):
            sp_dev += hp['recent']['runs_per_9'] - lg['sp_ra9']
            sp_n += hp['recent']['n']
        if ap and ap.get('recent'):
            sp_dev += ap['recent']['runs_per_9'] - lg['sp_ra9']
            sp_n += ap['recent']['n']

        proj = (neutral
                + TOTAL_W_OFFENSE * off_dev
                + TOTAL_W_PITCHER * TOTAL_SP_IP_FRAC * sp_dev)

        side = 'over' if proj > line else 'under'
        margin = abs(proj - line)

        pooled = (ht['totals'] or []) + (at['totals'] or [])
        emp = None
        if pooled:
            hits = sum(1 for t in pooled if (t > line if side == 'over' else t < line))
            emp = hits / len(pooled) * 100

        conf_margin = 50 + min(TOTAL_MARGIN_CAP, margin * TOTAL_MARGIN_SCALE)
        raw = round(0.55 * conf_margin + 0.45 * emp) if emp is not None else round(conf_margin)

        n = ht['n'] + at['n'] + sp_n
        cap = _cap_for(n)
        if cap is None:
            return None
        conf = min(raw, cap)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': line, 'confidence': conf,
            'projected': round(proj, 2), 'neutral': round(neutral, 2),
            'park_factor': park_factor, 'day_time_mult': round(day_time_mult, 3),
            'home_rpg': round(ht['runs_per_game'], 2), 'away_rpg': round(at['runs_per_game'], 2),
            'samples': {'home_team': ht['n'], 'away_team': at['n'],
                        'starters': sp_n, 'total': n},
        }

    def _analyze_team_hits(self, hp, ap, ht, at, lg, park_factor, day_time_mult=1.0):
        """
        Hits combinados del JUEGO (ambos equipos) — no del abridor. El casino del
        usuario no ofrece props de hits por pitcher, así que este mercado predice
        lo que sí es apostable, usando a los abridores como una señal más, igual
        que TOTAL (carreras) usa su RA/9.
        """
        if not (ht and at) or ht['hits_per_game'] is None or at['hits_per_game'] is None:
            return None

        # ── Línea: la MÁS CERCANA a la expectativa neutral (promedio de liga ×
        # park factor × día/hora). NUNCA se elige desde nuestra proyección.
        lg_team = lg['team_hits']
        neutral = 2 * lg_team * park_factor * day_time_mult
        line = min(TEAM_HITS_LINES, key=lambda l: abs(l - neutral))

        off_dev = (ht['hits_per_game'] - lg_team) + (at['hits_per_game'] - lg_team)

        sp_dev, sp_n = 0.0, 0
        if hp and hp.get('recent'):
            sp_dev += hp['recent']['hits_per_start'] - lg['sp_hits']
            sp_n += hp['recent']['n']
        if ap and ap.get('recent'):
            sp_dev += ap['recent']['hits_per_start'] - lg['sp_hits']
            sp_n += ap['recent']['n']

        proj = (neutral
                + TEAM_HITS_W_OFFENSE * off_dev
                + TEAM_HITS_W_PITCHER * TEAM_HITS_SP_IP_FRAC * sp_dev)

        side = 'over' if proj > line else 'under'
        margin = abs(proj - line)

        pooled = (ht.get('hits_totals') or []) + (at.get('hits_totals') or [])
        emp = None
        if pooled:
            hits = sum(1 for t in pooled if (t > line if side == 'over' else t < line))
            emp = hits / len(pooled) * 100

        conf_margin = 50 + min(TEAM_HITS_MARGIN_CAP, margin * TEAM_HITS_MARGIN_SCALE)
        raw = round(0.55 * conf_margin + 0.45 * emp) if emp is not None else round(conf_margin)

        n = ht['n'] + at['n'] + sp_n
        cap = _cap_for(n)
        if cap is None:
            return None
        conf = min(raw, cap)
        if conf < MIN_CONFIDENCE_EMIT:
            return None
        return {
            'side': side, 'line': line, 'confidence': conf,
            'projected': round(proj, 2), 'neutral': round(neutral, 2),
            'park_factor': park_factor, 'day_time_mult': round(day_time_mult, 3),
            'home_hpg': round(ht['hits_per_game'], 2), 'away_hpg': round(at['hits_per_game'], 2),
            'samples': {'home_team': ht['n'], 'away_team': at['n'],
                        'starters': sp_n, 'total': n},
        }

    # ── ensamblado ────────────────────────────────────────────────────────────

    @staticmethod
    def _note(market, d):
        if market == 'moneyline':
            return (f"Forma local {round(d['home_form'] * 100)}% · visitante {round(d['away_form'] * 100)}%"
                    + (f" · RA/9 abridores {d['home_sp_ra9']} vs {d['away_sp_ra9']}"
                       if d.get('home_sp_ra9') is not None and d.get('away_sp_ra9') is not None else ""))
        if market == 'nrfi':
            return (f"Prob. de 1er inning en blanco: {round(d['p_nrfi'] * 100)}% "
                    f"(local anota {round(d['p_home_scores'] * 100)}%, visitante {round(d['p_away_scores'] * 100)}%)")
        if market == 'total':
            return (f"Proyección {d['projected']} carreras vs neutral {d['neutral']} "
                    f"· park factor {d['park_factor']}")
        if market == 'hits':
            return (f"Proyección {d['projected']} hits vs neutral {d['neutral']} "
                    f"· local {d['home_hpg']}/juego · visitante {d['away_hpg']}/juego")
        return ""

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
                'odd': None,   # no existe fuente de momios para MLB
            })
        picks.sort(key=lambda p: -p['confidence'])
        return picks

    # ══════════════════════════════════════════════════════════════════════════
    #  Accuracy
    # ══════════════════════════════════════════════════════════════════════════

    def get_accuracy(self, redis_client=None, days: int = 7, min_confidence: int = 70,
                     league: str = None, end_date: str = None) -> dict:
        """
        Cruza los picks cacheados (`mlb_radar:{league}:{date}`) contra los
        resultados reales (`mlb:day:...` + boxscore del abridor). Cachea 1 h.
        """
        r = redis_client or self.r
        if league and league in LEAGUES:
            self.league = league
        end = (datetime.strptime(end_date, '%Y-%m-%d').date() if end_date
               else datetime.now(self.local_tz).date())

        cache_key = f'mlb_radar:accuracy:{self.league}:{end}:{days}:{min_confidence}'
        if r:
            cached = r.get(cache_key)
            if cached:
                return json.loads(cached)

        dates = [(end - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, days + 1)]
        picks, found, missing = [], [], []
        for d in dates:
            raw = r.get(f'mlb_radar:{self.league}:{d}') if r else None
            if not raw:
                missing.append(d)
                continue
            found.append(d)
            for s in json.loads(raw).get('suggestions', []):
                for p in s.get('top_picks', []):
                    if p['confidence'] >= min_confidence:
                        picks.append({**p, 'date': d, 'game_pk': s['game_pk']})

        results = {}
        for d in found:
            for g in self.get_day(d, force_refresh=False):
                results[g['gamePk']] = g

        by_market = defaultdict(lambda: {'wins': 0, 'losses': 0})
        by_band   = defaultdict(lambda: {'wins': 0, 'losses': 0})
        sides     = defaultdict(lambda: defaultdict(int))
        wins = losses = unsettled = 0

        for p in picks:
            outcome = self._evaluate_pick(p, results.get(p['game_pk']))
            sides[p['market']][p['side']] += 1
            if outcome is None:
                unsettled += 1
                continue
            band = f"{min(p['confidence'] // 10 * 10, 90)}+" if p['confidence'] >= 90 \
                else f"{p['confidence'] // 10 * 10}-{p['confidence'] // 10 * 10 + 9}"
            bucket = 'wins' if outcome == 'win' else 'losses'
            by_market[p['market']][bucket] += 1
            by_band[band][bucket] += 1
            if outcome == 'win':
                wins += 1
            else:
                losses += 1

        settled = wins + losses
        result = {
            'league': self.league, 'days': days, 'min_confidence': min_confidence,
            'dates_analyzed': found, 'dates_missing': missing,
            'total_picks': len(picks), 'settled': settled, 'unsettled': unsettled,
            'wins': wins, 'losses': losses,
            'accuracy': round(wins / settled * 100) if settled else None,
            'by_market': {
                m: {'wins': v['wins'], 'total': v['wins'] + v['losses'],
                    'accuracy': round(v['wins'] / (v['wins'] + v['losses']) * 100)}
                for m, v in by_market.items() if v['wins'] + v['losses']
            },
            'by_confidence_band': {
                b: {'wins': v['wins'], 'total': v['wins'] + v['losses'],
                    'accuracy': round(v['wins'] / (v['wins'] + v['losses']) * 100)}
                for b, v in sorted(by_band.items()) if v['wins'] + v['losses']
            },
            'side_ratio': {m: dict(v) for m, v in sides.items()},
        }

        if r:
            r.setex(cache_key, ACCURACY_TTL, json.dumps(result))
        return result

    def _evaluate_pick(self, pick, game):
        """'win' | 'loss' | None si el juego no terminó o falta el dato."""
        if game is None or not is_final(game):
            return None
        market, side, line = pick['market'], pick['side'], pick.get('line')
        hs = game['home'].get('score') or 0
        as_ = game['away'].get('score') or 0

        if market == 'moneyline':
            if hs == as_:
                return None
            return 'win' if (('home' if hs > as_ else 'away') == side) else 'loss'

        if market == 'nrfi':
            fh, fa = game['home'].get('first_inning_runs'), game['away'].get('first_inning_runs')
            if fh is None or fa is None:
                return None
            actual = 'yes' if (fh + fa) == 0 else 'no'
            return 'win' if actual == side else 'loss'

        if market == 'total':
            if line is None:
                return None
            total = hs + as_
            return 'win' if (total > line if side == 'over' else total < line) else 'loss'

        if market == 'hits':
            if line is None:
                return None
            h, a = game['home'].get('hits'), game['away'].get('hits')
            if h is None or a is None:
                return None
            total = h + a
            return 'win' if (total > line if side == 'over' else total < line) else 'loss'

        return None
