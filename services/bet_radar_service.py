import json
import math
from collections import defaultdict
from datetime import datetime, timedelta

import pytz
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam

from utils.referees import build_referee_keys

MIN_MATCHES = 4
MIN_DOW_SAMPLE = 30  # minimum fixtures per weekday to trust DOW multipliers
FINISHED_STATUSES = ('FT', 'AET', 'PEN')
VOID_STATUSES = ('PST', 'CANC', 'ABD', 'AWD', 'WO')
NOT_STARTED_STATUSES = ('NS', 'TBD')
ACCURACY_TTL = 3600  # 1 h — hit rates only shift as the day's fixtures settle
API_SPORTS_MEDIA = 'https://media.api-sports.io/football'  # logo CDN, no quota cost

# ── Goals: Poisson model, validated in ml/backtest_goals.py ──────────────────
# Fixed 2.5 line on purpose: picking the line from the model's own data
# (_best_line) leaned 97% under and produced overconfident hit rates.
GOALS_LINE        = 2.5
GOALS_SHRINK_K    = 30   # pseudo-matches pulling a team toward its league mean — best Brier in the backtest
GOALS_TEAM_WINDOW = 20   # matches per team for attack/defense ratings
GOALS_LEAGUE_MIN  = 30   # matches before trusting a league's own home/away means
GOALS_MIN_CONF    = 60
GOALS_MAX_CONF    = 90

LINES = {
    'corners':      [8.5, 9.5, 10.5, 11.5],
    'goals':        [1.5, 2.5, 3.5],
    'yellow_cards': [2.5, 3.5, 4.5],
}

MARKET_LABELS = {
    'corners':      lambda m: f"Corners {'Over' if m['side']=='over' else 'Under'} {m['line']}",
    'goals':        lambda m: f"Goals {'Over' if m['side']=='over' else 'Under'} {m['line']}",
    'yellow_cards': lambda m: f"Yellow Cards {'Over' if m['side']=='over' else 'Under'} {m['line']}",
    'btts':         lambda m: f"BTTS {'Sí' if m['side']=='yes' else 'No'}",
}

MARKET_NOTES = {
    'corners': lambda m, hn, an: (
        f"Local {hn}: {m['home_avg']} córners · Visitante {an}: {m['away_avg']}"
        + (f" · H2H: {m['h2h_avg']}" if m.get('h2h_avg') else "")
    ),
    'goals': lambda m, hn, an: (
        f"Proyección: {m['projected']} goles"
        + (f" · xG combinado: {m['xg_avg']}" if m.get('xg_avg') else "")
    ),
    'yellow_cards': lambda m, hn, an: (
        f"Árbitro: {m['referee_avg']} tarjetas promedio ({m['referee_sample']} partidos)"
        if m.get('referee_avg') else f"Proyección: {m['projected']} tarjetas"
    ),
    'btts': lambda m, hn, an: f"Ambos anotan en el {m['confidence']}% de los partidos analizados",
}


def settle_pick(market, side, line, home_goals, away_goals, corners, cards, final):
    """
    Single source of truth for grading a pick, live or final.
    state: won | lost | winning | losing | unknown. won/lost can be decided
    before full time: an over that crossed its line (or BTTS yes once both
    scored) can't lose anymore; an under that broke its line can't win.
    margin = actual - line, i.e. how far over (+) or under (-) the line it landed.
    """
    hg, ag = home_goals or 0, away_goals or 0

    if market == 'btts':
        if hg > 0 and ag > 0:
            state = 'won' if side == 'yes' else 'lost'
        elif final:
            state = 'won' if side == 'no' else 'lost'
        else:
            state = 'winning' if side == 'no' else 'losing'
        return {'state': state, 'actual': None, 'margin': None}

    actual = {'goals': hg + ag, 'corners': corners, 'yellow_cards': cards}.get(market)
    if actual is None or line is None:
        return {'state': 'unknown', 'actual': actual, 'margin': None}

    if side == 'over':
        state = 'won' if actual > line else ('lost' if final else 'losing')
    else:
        state = 'lost' if actual > line else ('won' if final else 'winning')
    return {'state': state, 'actual': actual, 'margin': round(actual - line, 1)}


class BetRadarService:
    def __init__(self, db: Session):
        self.db = db

    # ── public ────────────────────────────────────────────────────────────────

    def get_suggestions(self, date_str: str):
        return self._run_pipeline(self._get_fixtures_for_date(date_str), date_str)

    def get_suggestions_from_list(self, fixtures: list, date_str: str):
        return self._run_pipeline(fixtures, date_str)

    def get_accuracy(self, redis_client, days: int = 7, min_confidence: int = 70, end_date: str = None):
        """
        Cross-references cached predictions (Redis) against actual results (DB).
        Returns overall + per-market hit rates for the trailing `days` window,
        excluding today (matches may still be in play).

        Result is cached — it only shifts as the day's fixtures settle.
        """
        tz  = pytz.timezone('America/Mexico_City')
        end = (datetime.strptime(end_date, '%Y-%m-%d').date() if end_date
               else datetime.now(tz).date())

        cache_key = f'bet_radar:accuracy:{end}:{days}:{min_confidence}'
        if redis_client:
            cached = redis_client.get(cache_key)
            if cached:
                return json.loads(cached)

        dates = [(end - timedelta(days=i)).strftime('%Y-%m-%d') for i in range(1, days + 1)]

        picks, found, missing = [], [], []
        for d in dates:
            raw = redis_client.get(f'bet_radar:{d}') if redis_client else None
            if not raw:
                missing.append(d)
                continue
            found.append(d)
            for s in json.loads(raw).get('suggestions', []):
                for p in s.get('top_picks', []):
                    if p['confidence'] >= min_confidence:
                        picks.append({
                            'date': d, 'fixture_id': s['fixture_id'],
                            'market': p['market'], 'side': p['side'],
                            'line': p.get('line'), 'confidence': p['confidence'],
                        })

        by_market = defaultdict(lambda: {'wins': 0, 'losses': 0})
        wins = losses = unsettled = 0

        if picks:
            results = self._results_batch(list({p['fixture_id'] for p in picks}))
            for p in picks:
                outcome = self._evaluate_pick(p, results.get(p['fixture_id']))
                if outcome is None:
                    unsettled += 1
                    continue
                if outcome == 'win':
                    wins += 1
                    by_market[p['market']]['wins'] += 1
                else:
                    losses += 1
                    by_market[p['market']]['losses'] += 1

        settled = wins + losses
        result = {
            'days': days,
            'min_confidence': min_confidence,
            'dates_analyzed': found,
            'dates_missing': missing,
            'total_picks': len(picks),
            'settled': settled,
            'unsettled': unsettled,
            'wins': wins,
            'losses': losses,
            'accuracy': round(wins / settled * 100) if settled else None,
            'by_market': {
                m: {
                    'wins': v['wins'],
                    'total': v['wins'] + v['losses'],
                    'accuracy': round(v['wins'] / (v['wins'] + v['losses']) * 100),
                }
                for m, v in by_market.items() if (v['wins'] + v['losses']) > 0
            },
        }

        if redis_client:
            redis_client.setex(cache_key, ACCURACY_TTL, json.dumps(result))
        return result

    def _results_batch(self, fixture_ids: list) -> dict:
        """One query for every fixture referenced by a pick — {fixture_id: row}."""
        if not fixture_ids:
            return {}
        rows = self.db.execute(
            text("""
                SELECT
                    f.id, f.home_goals, f.away_goals, f.status,
                    hs.corners       AS home_corners,
                    aws.corners      AS away_corners,
                    hs.yellow_cards  AS home_cards,
                    aws.yellow_cards AS away_cards
                FROM fixtures f
                LEFT JOIN fixture_team_stats hs  ON hs.fixture_id = f.id AND hs.is_home = true
                LEFT JOIN fixture_team_stats aws ON aws.fixture_id = f.id AND aws.is_home = false
                WHERE f.id IN :ids
            """).bindparams(bindparam('ids', expanding=True)),
            {'ids': fixture_ids},
        ).fetchall()
        return {row.id: row for row in rows}

    @staticmethod
    def _evaluate_pick(pick, row):
        """'win' | 'loss' | None when the fixture is unfinished or lacks stats."""
        if row is None or row.status not in FINISHED_STATUSES:
            return None

        both = lambda h, a: None if h is None or a is None else h + a
        result = settle_pick(
            pick['market'], pick['side'], pick['line'], row.home_goals, row.away_goals,
            both(row.home_corners, row.away_corners), both(row.home_cards, row.away_cards), final=True,
        )
        return {'won': 'win', 'lost': 'loss'}.get(result['state'])

    def _run_pipeline(self, fixtures, date_str: str):
        if not fixtures:
            return {'date': date_str, 'fixtures_analyzed': 0, 'suggestions': [], 'parlay_suggestion': None}

        dow_mults = self._compute_dow_multipliers()

        home_ids = [f.home_team_id for f in fixtures]
        away_ids = [f.away_team_id for f in fixtures]
        pairs    = [(f.home_team_id, f.away_team_id) for f in fixtures]
        referees = list({f.referee for f in fixtures if f.referee})

        home_map = self._locality_batch(home_ids, is_home=True)
        away_map = self._locality_batch(away_ids, is_home=False)
        h2h_map  = self._h2h_batch(pairs)
        ref_map  = self._referee_batch(referees)
        team_goals   = self._team_goals_batch(home_ids + away_ids)
        league_means = self._league_goal_means()

        results = []
        for f in fixtures:
            pair_key  = (min(f.home_team_id, f.away_team_id), max(f.home_team_id, f.away_team_id))
            ref_stats = ref_map.get(f.referee) if f.referee else None
            home      = home_map.get(f.home_team_id, [])
            away      = away_map.get(f.away_team_id, [])
            h2h       = h2h_map.get(pair_key, [])
            league_id = getattr(f, 'league_id', None)
            dow       = f.date_utc.weekday() if f.date_utc else None  # 0=Mon, 6=Sun

            analysis = self._analyze_fixture(f, home, away, h2h, ref_stats, league_id, dow, dow_mults,
                                             team_goals, league_means)
            if analysis and analysis['top_picks']:
                results.append(analysis)

        results.sort(key=lambda x: x['top_picks'][0]['confidence'] if x['top_picks'] else 0, reverse=True)

        return {
            'date': date_str,
            'fixtures_analyzed': len(fixtures),
            'suggestions': results,
            'parlay_suggestion': self._build_parlay(results),
        }

    # ── DOW multipliers ───────────────────────────────────────────────────────

    def _compute_dow_multipliers(self) -> dict:
        """
        Derives per-weekday multipliers from DB history.
        {dow: {'goals': float, 'corners': float, 'cards': float}}
        dow uses Python weekday() — 0=Mon, 6=Sun.
        Multiplier = day_avg / global_avg. Falls back to 1.0 when sample < MIN_DOW_SAMPLE.
        """
        rows = self.db.execute(text("""
            SELECT
                ((EXTRACT(DOW FROM f.date_utc AT TIME ZONE 'America/Mexico_City')::int + 6) % 7) AS dow,
                COUNT(*) AS sample,
                AVG(COALESCE(f.home_goals, 0) + COALESCE(f.away_goals, 0)) AS avg_goals,
                AVG(CASE WHEN hs.corners IS NOT NULL AND aws.corners IS NOT NULL
                         THEN hs.corners + aws.corners END) AS avg_corners,
                AVG(CASE WHEN hs.yellow_cards IS NOT NULL AND aws.yellow_cards IS NOT NULL
                         THEN hs.yellow_cards + aws.yellow_cards END) AS avg_cards
            FROM fixtures f
            JOIN fixture_team_stats hs  ON hs.fixture_id = f.id AND hs.is_home = true
            JOIN fixture_team_stats aws ON aws.fixture_id = f.id AND aws.is_home = false
            WHERE f.status IN ('FT','AET','PEN')
            GROUP BY dow
        """)).fetchall()

        if not rows:
            return {}

        valid_goals   = [float(r.avg_goals)   for r in rows if r.avg_goals]
        valid_corners = [float(r.avg_corners) for r in rows if r.avg_corners]
        valid_cards   = [float(r.avg_cards)   for r in rows if r.avg_cards]

        global_goals   = sum(valid_goals)   / len(valid_goals)   if valid_goals   else 1.0
        global_corners = sum(valid_corners) / len(valid_corners) if valid_corners else 1.0
        global_cards   = sum(valid_cards)   / len(valid_cards)   if valid_cards   else 1.0

        result = {}
        for r in rows:
            sample = int(r.sample)
            dow    = int(r.dow)
            result[dow] = {
                'goals':   round(float(r.avg_goals)   / global_goals,   3) if sample >= MIN_DOW_SAMPLE and r.avg_goals   else 1.0,
                'corners': round(float(r.avg_corners) / global_corners, 3) if sample >= MIN_DOW_SAMPLE and r.avg_corners else 1.0,
                'cards':   round(float(r.avg_cards)   / global_cards,   3) if sample >= MIN_DOW_SAMPLE and r.avg_cards   else 1.0,
            }
        return result

    # ── batch data queries ────────────────────────────────────────────────────

    def _get_fixtures_for_date(self, date_str: str):
        return self.db.execute(text("""
            SELECT
                f.id, f.home_team_id, f.home_team_name,
                f.away_team_id, f.away_team_name,
                f.home_goals, f.away_goals, f.date_utc,
                f.referee, f.league_id, f.status
            FROM fixtures f
            WHERE DATE(f.date_utc AT TIME ZONE 'America/Mexico_City') = :date
              AND f.status IN ('FT','AET','PEN')
            ORDER BY f.date_utc
        """), {'date': date_str}).fetchall()

    def _locality_batch(self, team_ids: list, is_home: bool, limit: int = 8) -> dict:
        if not team_ids:
            return {}
        ids_str = ','.join(str(i) for i in set(team_ids))
        rows = self.db.execute(text(f"""
            SELECT * FROM (
                SELECT
                    ts.team_id,
                    f.league_id,
                    CASE WHEN ts.corners IS NOT NULL AND opp.corners IS NOT NULL
                         THEN ts.corners + opp.corners ELSE NULL END            AS total_corners,
                    COALESCE(f.home_goals, 0) + COALESCE(f.away_goals, 0)      AS total_goals,
                    CASE WHEN ts.yellow_cards IS NOT NULL AND opp.yellow_cards IS NOT NULL
                         THEN ts.yellow_cards + opp.yellow_cards ELSE NULL END  AS total_yellows,
                    CASE WHEN f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL
                         THEN (CASE WHEN f.home_goals > 0 AND f.away_goals > 0 THEN 1 ELSE 0 END)
                         ELSE NULL END                                           AS btts,
                    CASE WHEN ts.expected_goals IS NOT NULL AND opp.expected_goals IS NOT NULL
                         THEN CAST(ts.expected_goals AS FLOAT)
                              + CAST(opp.expected_goals AS FLOAT)
                         ELSE NULL END                                           AS total_xg,
                    f.date_utc,
                    ROW_NUMBER() OVER (PARTITION BY ts.team_id ORDER BY f.date_utc DESC) AS rn
                FROM fixture_team_stats ts
                JOIN fixtures f ON f.id = ts.fixture_id
                JOIN fixture_team_stats opp
                    ON opp.fixture_id = ts.fixture_id AND opp.is_home != ts.is_home
                WHERE ts.team_id IN ({ids_str})
                  AND ts.is_home = :is_home
                  AND f.status IN ('FT','AET','PEN')
            ) sub
            WHERE rn <= :limit
            ORDER BY team_id, date_utc DESC
        """), {'is_home': is_home, 'limit': limit}).fetchall()

        result: dict = defaultdict(list)
        for row in rows:
            result[row.team_id].append(row)
        return dict(result)

    def _team_goals_batch(self, team_ids: list, limit: int = GOALS_TEAM_WINDOW) -> dict:
        """{team_id: rows} — last `limit` finished matches at any venue, goals scored/conceded."""
        if not team_ids:
            return {}
        rows = self.db.execute(text("""
            SELECT * FROM (
                SELECT t.team_id, f.league_id, t.is_home,
                       CASE WHEN t.is_home THEN f.home_goals ELSE f.away_goals END AS scored,
                       CASE WHEN t.is_home THEN f.away_goals ELSE f.home_goals END AS conceded,
                       ROW_NUMBER() OVER (PARTITION BY t.team_id ORDER BY f.date_utc DESC) AS rn
                FROM fixtures f
                CROSS JOIN LATERAL (VALUES (f.home_team_id, true), (f.away_team_id, false)) AS t(team_id, is_home)
                WHERE t.team_id IN :ids
                  AND f.status IN ('FT','AET','PEN')
                  AND f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL
            ) sub
            WHERE rn <= :limit
        """).bindparams(bindparam('ids', expanding=True)), {'ids': list(set(team_ids)), 'limit': limit}).fetchall()
        result: dict = defaultdict(list)
        for row in rows:
            result[row.team_id].append(row)
        return dict(result)

    def _league_goal_means(self) -> dict:
        """{league_id: (home_mean, away_mean)} for leagues with enough matches, plus None -> global means."""
        rows = self.db.execute(text("""
            SELECT league_id, COUNT(*) AS n, AVG(home_goals) AS h, AVG(away_goals) AS a
            FROM fixtures
            WHERE status IN ('FT','AET','PEN') AND home_goals IS NOT NULL AND away_goals IS NOT NULL
            GROUP BY league_id
        """)).fetchall()
        total = sum(r.n for r in rows) or 1
        means = {None: (sum(float(r.h) * r.n for r in rows) / total if rows else 1.4,
                        sum(float(r.a) * r.n for r in rows) / total if rows else 1.1)}
        for r in rows:
            if r.n >= GOALS_LEAGUE_MIN:
                means[r.league_id] = (float(r.h), float(r.a))
        return means

    def _h2h_batch(self, pairs: list, limit: int = 8) -> dict:
        if not pairs:
            return {}
        all_ids = set()
        for t1, t2 in pairs:
            all_ids.add(t1)
            all_ids.add(t2)
        ids_str = ','.join(str(i) for i in all_ids)

        canonical   = {(min(t1, t2), max(t1, t2)) for t1, t2 in pairs}
        pair_filter = ','.join(f'({a},{b})' for a, b in canonical)

        rows = self.db.execute(text(f"""
            SELECT * FROM (
                SELECT
                    f.home_team_id, f.away_team_id,
                    CASE WHEN hs.corners IS NOT NULL AND aws.corners IS NOT NULL
                         THEN hs.corners + aws.corners ELSE NULL END             AS total_corners,
                    COALESCE(f.home_goals, 0) + COALESCE(f.away_goals, 0)       AS total_goals,
                    CASE WHEN hs.yellow_cards IS NOT NULL AND aws.yellow_cards IS NOT NULL
                         THEN hs.yellow_cards + aws.yellow_cards ELSE NULL END   AS total_yellows,
                    CASE WHEN f.home_goals IS NOT NULL AND f.away_goals IS NOT NULL
                         THEN (CASE WHEN f.home_goals > 0 AND f.away_goals > 0 THEN 1 ELSE 0 END)
                         ELSE NULL END                                            AS btts,
                    f.date_utc,
                    LEAST(f.home_team_id, f.away_team_id)    AS team_min,
                    GREATEST(f.home_team_id, f.away_team_id) AS team_max,
                    ROW_NUMBER() OVER (
                        PARTITION BY
                            LEAST(f.home_team_id, f.away_team_id),
                            GREATEST(f.home_team_id, f.away_team_id)
                        ORDER BY f.date_utc DESC
                    ) AS rn
                FROM fixtures f
                JOIN fixture_team_stats hs  ON hs.fixture_id = f.id AND hs.is_home = true
                JOIN fixture_team_stats aws ON aws.fixture_id = f.id AND aws.is_home = false
                WHERE f.home_team_id IN ({ids_str})
                  AND f.away_team_id IN ({ids_str})
                  AND f.status IN ('FT','AET','PEN')
            ) sub
            WHERE rn <= :limit
              AND (team_min, team_max) IN ({pair_filter})
            ORDER BY team_min, team_max, date_utc DESC
        """), {'limit': limit}).fetchall()

        result: dict = defaultdict(list)
        for row in rows:
            key = (min(row.home_team_id, row.away_team_id), max(row.home_team_id, row.away_team_id))
            result[key].append(row)
        return dict(result)

    def _referee_batch(self, referees: list) -> dict:
        """
        Avg yellows per referee, pooling every spelling of the same person
        (utils/referees.py). Clashing or unkeyed names fall back to exact match.
        """
        if not referees:
            return {}
        all_names = [r[0] for r in self.db.execute(
            text("SELECT DISTINCT referee FROM fixtures WHERE referee IS NOT NULL")
        ).fetchall()]
        keys, clashing = build_referee_keys(set(all_names) | set(referees))
        by_key = defaultdict(set)
        for name, key in keys.items():
            if key and key not in clashing:
                by_key[key].add(name)

        variants = {}
        for ref in referees:
            key = keys.get(ref)
            variants[ref] = by_key[key] if key in by_key else {ref}

        rows = self.db.execute(
            text("""
                SELECT
                    f.referee,
                    COUNT(*) AS sample_size,
                    AVG(CAST(hs.yellow_cards AS FLOAT)
                        + CAST(aws.yellow_cards AS FLOAT)) AS avg_yellows
                FROM fixtures f
                JOIN fixture_team_stats hs  ON hs.fixture_id = f.id AND hs.is_home = true
                JOIN fixture_team_stats aws ON aws.fixture_id = f.id AND aws.is_home = false
                WHERE f.referee IN :refs
                  AND hs.yellow_cards IS NOT NULL
                  AND aws.yellow_cards IS NOT NULL
                GROUP BY f.referee
            """).bindparams(bindparam('refs', expanding=True)),
            {'refs': sorted(set().union(*variants.values()))},
        ).fetchall()
        per_name = {row.referee: (int(row.sample_size), float(row.avg_yellows)) for row in rows}

        result = {}
        for ref, names in variants.items():
            stats = [per_name[n] for n in names if n in per_name]
            sample = sum(n for n, _ in stats)
            if sample >= MIN_MATCHES:
                result[ref] = {
                    'avg_yellows': round(sum(n * avg for n, avg in stats) / sample, 2),
                    'sample_size': sample,
                }
        return result

    # ── analysis helpers ──────────────────────────────────────────────────────

    @staticmethod
    def _vals(rows, col):
        return [getattr(r, col) for r in rows if getattr(r, col) is not None]

    @staticmethod
    def _avg(lst):
        return round(sum(lst) / len(lst), 1) if lst else None

    @staticmethod
    def _best_line(values, candidates, default):
        if not values:
            return default
        avg = sum(values) / len(values)
        for line in sorted(candidates):
            if avg < line:
                return line
        return candidates[-1]

    @staticmethod
    def _hit_rate(values, line, side):
        valid = [v for v in values if v is not None]
        if not valid:
            return None
        hits = sum(1 for v in valid if (v > line if side == 'over' else v < line))
        return round(hits / len(valid) * 100)

    def _weighted_confidence(self, home_vals, away_vals, h2h_vals, line, side):
        slots = [(home_vals, 0.35), (away_vals, 0.35), (h2h_vals, 0.30)]
        parts, weights = [], []
        for vals, w in slots:
            valid = [v for v in vals if v is not None]
            if len(valid) >= MIN_MATCHES:
                rate = self._hit_rate(valid, line, side)
                if rate is not None:
                    parts.append(rate)
                    weights.append(w)
        if not parts:
            return None
        total_w = sum(weights)
        return round(sum(p * w / total_w for p, w in zip(parts, weights)))

    def _league_avg(self, rows, col, league_id) -> float | None:
        """
        League-blended average: 70% same-league + 30% other-league.
        Falls back to overall average when same-league has < MIN_MATCHES.
        """
        same  = [getattr(r, col) for r in rows if getattr(r, col) is not None and getattr(r, 'league_id', None) == league_id]
        other = [getattr(r, col) for r in rows if getattr(r, col) is not None and getattr(r, 'league_id', None) != league_id]
        all_v = same + other
        if not all_v:
            return None
        if len(same) >= MIN_MATCHES:
            s_avg = sum(same) / len(same)
            o_avg = sum(other) / len(other) if other else s_avg
            return s_avg * 0.70 + o_avg * 0.30
        return sum(all_v) / len(all_v)

    # ── per-market analysis ───────────────────────────────────────────────────

    def _analyze_corners(self, home, away, h2h, league_id, dow_mult):
        hv = self._vals(home, 'total_corners')
        av = self._vals(away, 'total_corners')
        xv = self._vals(h2h,  'total_corners')
        if len(hv + av + xv) < MIN_MATCHES:
            return None

        h_avg = self._league_avg(home, 'total_corners', league_id)
        a_avg = self._league_avg(away, 'total_corners', league_id)
        avg   = ((h_avg + a_avg) / 2 if h_avg is not None and a_avg is not None
                 else sum(hv + av + xv) / len(hv + av + xv))
        avg  *= dow_mult

        line = self._best_line(hv + av + xv, LINES['corners'], 9.5)
        side = 'under' if avg < line else 'over'
        conf = self._weighted_confidence(hv, av, xv, line, side)

        if conf is None or conf < 58:
            return None
        return {
            'projected': round(avg, 1),
            'line': line, 'side': side, 'confidence': conf,
            'home_avg': self._avg(hv), 'away_avg': self._avg(av), 'h2h_avg': self._avg(xv),
            'samples': {'home': len(hv), 'away': len(av), 'h2h': len(xv)},
        }

    @staticmethod
    def _goal_rating(rows, key, league_means):
        """
        Attack (key='scored') or defense (key='conceded') vs the league mean of
        each match's venue, shrunk toward 1.0 (= league average) by GOALS_SHRINK_K.
        """
        total = 0.0
        for r in rows:
            h, a = league_means.get(r.league_id, league_means[None])
            # scoring at home is judged against the league's home mean; conceding at home against its away mean
            mean = (h if r.is_home else a) if key == 'scored' else (a if r.is_home else h)
            total += getattr(r, key) / max(mean, 0.3)
        return (total + GOALS_SHRINK_K) / (len(rows) + GOALS_SHRINK_K)

    def _analyze_goals(self, home_id, away_id, home, away, h2h, league_id, team_goals, league_means):
        hrows, arows = team_goals.get(home_id, []), team_goals.get(away_id, [])
        if len(hrows) + len(arows) < MIN_MATCHES:
            return None

        mu_h, mu_a = league_means.get(league_id, league_means[None])
        lam = (mu_h * self._goal_rating(hrows, 'scored', league_means) * self._goal_rating(arows, 'conceded', league_means)
               + mu_a * self._goal_rating(arows, 'scored', league_means) * self._goal_rating(hrows, 'conceded', league_means))
        p_over = 1 - sum(math.exp(-lam) * lam ** k / math.factorial(k) for k in range(math.floor(GOALS_LINE) + 1))

        side = 'over' if p_over >= 0.5 else 'under'
        conf = min(round(max(p_over, 1 - p_over) * 100), GOALS_MAX_CONF)
        if conf < GOALS_MIN_CONF:
            return None

        hv, av, xv = self._vals(home, 'total_goals'), self._vals(away, 'total_goals'), self._vals(h2h, 'total_goals')
        return {
            'projected': round(lam, 1), 'p_over': round(p_over, 3), 'model': 'poisson',
            'line': GOALS_LINE, 'side': side, 'confidence': conf,
            'home_avg': self._avg(hv), 'away_avg': self._avg(av), 'h2h_avg': self._avg(xv),
            'samples': {'home': len(hrows), 'away': len(arows), 'h2h': len(xv)},
        }

    def _analyze_yellow_cards(self, home, away, h2h, ref_stats, league_id, dow_mult):
        hv = self._vals(home, 'total_yellows')
        av = self._vals(away, 'total_yellows')
        xv = self._vals(h2h,  'total_yellows')
        all_v = hv + av + xv

        # The referee drives the card count, so no referee history means no pick.
        if ref_stats is None:
            return None
        has_team_data = len(all_v) >= MIN_MATCHES
        ref_avg = ref_stats['avg_yellows']

        if has_team_data:
            h_avg    = self._league_avg(home, 'total_yellows', league_id)
            a_avg    = self._league_avg(away, 'total_yellows', league_id)
            team_avg = ((h_avg + a_avg) / 2 if h_avg is not None and a_avg is not None
                        else sum(all_v) / len(all_v))
            ref_w    = min(0.50, ref_stats['sample_size'] / 20)
            avg      = team_avg * (1 - ref_w) + ref_avg * ref_w
        else:
            avg = ref_avg

        avg *= dow_mult

        line = self._best_line(all_v if all_v else [avg], LINES['yellow_cards'], 3.5)
        side = 'under' if avg < line else 'over'
        ref_side = 'under' if ref_avg < line else 'over'

        conf = self._weighted_confidence(hv, av, xv, line, side) if has_team_data else None
        if conf is not None:
            if ref_side == side:
                conf = min(90, conf + 8)
        else:
            conf = min(75, ref_stats['sample_size'] * 5)
            side = ref_side

        if conf < 58:
            return None
        return {
            'projected': round(avg, 1),
            'line': line, 'side': side, 'confidence': conf,
            'referee_avg': ref_avg,
            'referee_sample': ref_stats['sample_size'],
            'home_avg': self._avg(hv), 'away_avg': self._avg(av),
            'samples': {
                'home': len(hv), 'away': len(av), 'h2h': len(xv),
                'referee': ref_stats['sample_size'],
            },
        }

    def _analyze_btts(self, home, away, h2h):
        # BTTS is a binary rate — DOW and league weighting don't apply
        hv = self._vals(home, 'btts')
        av = self._vals(away, 'btts')
        xv = self._vals(h2h,  'btts')

        slots = [(hv, 0.35), (av, 0.35), (xv, 0.30)]
        parts, weights = [], []
        for vals, w in slots:
            if len(vals) >= MIN_MATCHES:
                parts.append(round(sum(vals) / len(vals) * 100))
                weights.append(w)

        if not parts:
            return None

        total_w  = sum(weights)
        yes_rate = round(sum(p * w / total_w for p, w in zip(parts, weights)))
        side     = 'yes' if yes_rate >= 50 else 'no'
        conf     = yes_rate if side == 'yes' else (100 - yes_rate)

        if conf < 60:
            return None
        return {
            'side': side, 'confidence': conf,
            'samples': {'home': len(hv), 'away': len(av), 'h2h': len(xv)},
        }

    # ── orchestration ─────────────────────────────────────────────────────────

    def _analyze_fixture(self, fixture, home, away, h2h, ref_stats, league_id, dow, dow_mults,
                         team_goals, league_means):
        hn = fixture.home_team_name
        an = fixture.away_team_name

        # No DOW multiplier for goals: the Poisson model was validated without it.
        dow_corners = dow_mults.get(dow, {}).get('corners', 1.0) if dow is not None else 1.0
        dow_cards   = dow_mults.get(dow, {}).get('cards',   1.0) if dow is not None else 1.0

        markets = {}
        for key, fn in [
            ('corners',      lambda: self._analyze_corners(home, away, h2h, league_id, dow_corners)),
            ('goals',        lambda: self._analyze_goals(fixture.home_team_id, fixture.away_team_id, home, away, h2h,
                                                             league_id, team_goals, league_means)),
            ('yellow_cards', lambda: self._analyze_yellow_cards(home, away, h2h, ref_stats, league_id, dow_cards)),
            ('btts',         lambda: self._analyze_btts(home, away, h2h)),
        ]:
            result = fn()
            if result:
                markets[key] = result

        top_picks = self._build_top_picks(markets, hn, an)

        return {
            'fixture_id':  fixture.id,
            'league':      {'id': league_id, 'name': getattr(fixture, 'league_name', None),
                            'logo': f'{API_SPORTS_MEDIA}/leagues/{league_id}.png' if league_id else None},
            'home_team':   {'id': fixture.home_team_id, 'name': hn, 'logo': f'{API_SPORTS_MEDIA}/teams/{fixture.home_team_id}.png'},
            'away_team':   {'id': fixture.away_team_id, 'name': an, 'logo': f'{API_SPORTS_MEDIA}/teams/{fixture.away_team_id}.png'},
            'date':        fixture.date_utc.isoformat() if fixture.date_utc else None,
            'referee':     fixture.referee,
            'result':      f"{fixture.home_goals}-{fixture.away_goals}" if fixture.home_goals is not None else None,
            'markets':     markets,
            'top_picks':   top_picks,
            'h2h_count':   len(h2h),
            'home_locality_count': len(home),
            'away_locality_count': len(away),
        }

    def _build_top_picks(self, markets: dict, home_name: str, away_name: str):
        picks = []
        for market, data in markets.items():
            picks.append({
                'market':     market,
                'label':      MARKET_LABELS[market](data),
                'note':       MARKET_NOTES[market](data, home_name, away_name),
                'confidence': data['confidence'],
                'side':       data.get('side'),
                'line':       data.get('line'),
                'samples':    data.get('samples', {}),
            })
        picks.sort(key=lambda p: -p['confidence'])
        return picks

    def _build_parlay(self, results):
        candidates = [
            {**pick, 'fixture': f"{r['home_team']['name']} vs {r['away_team']['name']}",
             'fixture_id': r['fixture_id']}
            for r in results
            for pick in r['top_picks'][:1]
            if pick['confidence'] >= 60
        ]
        candidates.sort(key=lambda p: -p['confidence'])

        parlay, seen = [], set()
        for pick in candidates:
            if pick['fixture_id'] not in seen:
                parlay.append(pick)
                seen.add(pick['fixture_id'])
            if len(parlay) == 2:
                break

        if len(parlay) < 2:
            return None

        p1, p2 = parlay[0]['confidence'] / 100, parlay[1]['confidence'] / 100
        return {
            'picks': parlay,
            'combined_probability': round(p1 * p2 * 100),
        }
