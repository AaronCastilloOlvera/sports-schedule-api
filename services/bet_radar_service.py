from collections import defaultdict
from sqlalchemy.orm import Session
from sqlalchemy import text, bindparam

MIN_MATCHES = 4

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


class BetRadarService:
    def __init__(self, db: Session):
        self.db = db

    # ── public ────────────────────────────────────────────────────────────────

    def get_suggestions(self, date_str: str):
        """On-demand endpoint: queries DB for finished fixtures on date_str."""
        return self._run_pipeline(self._get_fixtures_for_date(date_str), date_str)

    def get_suggestions_from_list(self, fixtures: list, date_str: str):
        """Nightly worker: fixtures come from Redis (NS matches), not the DB."""
        return self._run_pipeline(fixtures, date_str)

    def _run_pipeline(self, fixtures, date_str: str):
        if not fixtures:
            return {'date': date_str, 'fixtures_analyzed': 0, 'suggestions': [], 'parlay_suggestion': None}

        # 4 batch queries total instead of 4×N per-fixture queries
        home_ids  = [f.home_team_id for f in fixtures]
        away_ids  = [f.away_team_id for f in fixtures]
        pairs     = [(f.home_team_id, f.away_team_id) for f in fixtures]
        referees  = list({f.referee for f in fixtures if f.referee})

        home_map = self._locality_batch(home_ids, is_home=True)
        away_map = self._locality_batch(away_ids, is_home=False)
        h2h_map  = self._h2h_batch(pairs)
        ref_map  = self._referee_batch(referees)

        results = []
        for f in fixtures:
            pair_key  = (min(f.home_team_id, f.away_team_id), max(f.home_team_id, f.away_team_id))
            ref_stats = ref_map.get(f.referee) if f.referee else None
            home      = home_map.get(f.home_team_id, [])
            away      = away_map.get(f.away_team_id, [])
            h2h       = h2h_map.get(pair_key, [])

            analysis = self._analyze_fixture(f, home, away, h2h, ref_stats)
            if analysis and analysis['top_picks']:
                results.append(analysis)

        results.sort(
            key=lambda x: x['top_picks'][0]['confidence'] if x['top_picks'] else 0,
            reverse=True,
        )

        return {
            'date': date_str,
            'fixtures_analyzed': len(fixtures),
            'suggestions': results,
            'parlay_suggestion': self._build_parlay(results),
        }

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
        """One query for all teams — returns {team_id: [rows]}."""
        if not team_ids:
            return {}
        ids_str = ','.join(str(i) for i in set(team_ids))
        rows = self.db.execute(text(f"""
            SELECT * FROM (
                SELECT
                    ts.team_id,
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

    def _h2h_batch(self, pairs: list, limit: int = 8) -> dict:
        """One query for all H2H pairs — returns {(min_id, max_id): [rows]}."""
        if not pairs:
            return {}
        all_ids = set()
        for t1, t2 in pairs:
            all_ids.add(t1)
            all_ids.add(t2)
        ids_str = ','.join(str(i) for i in all_ids)

        canonical = {(min(t1, t2), max(t1, t2)) for t1, t2 in pairs}
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
        """One query for all referees — returns {referee: stats_dict}."""
        if not referees:
            return {}
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
            {'refs': referees},
        ).fetchall()

        result = {}
        for row in rows:
            if int(row.sample_size) >= MIN_MATCHES:
                result[row.referee] = {
                    'avg_yellows': round(float(row.avg_yellows), 2),
                    'sample_size': int(row.sample_size),
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
        slots = [
            (home_vals, 0.35),
            (away_vals, 0.35),
            (h2h_vals,  0.30),
        ]
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

    # ── per-market analysis ───────────────────────────────────────────────────

    def _analyze_corners(self, home, away, h2h):
        hv = self._vals(home, 'total_corners')
        av = self._vals(away, 'total_corners')
        xv = self._vals(h2h,  'total_corners')
        all_v = hv + av + xv
        if len(all_v) < MIN_MATCHES:
            return None

        avg  = sum(all_v) / len(all_v)
        line = self._best_line(all_v, LINES['corners'], 9.5)
        side = 'under' if avg < line else 'over'
        conf = self._weighted_confidence(hv, av, xv, line, side)

        if conf is None or conf < 58:
            return None
        return {
            'projected': round(avg, 1),
            'line': line, 'side': side, 'confidence': conf,
            'home_avg': self._avg(hv),
            'away_avg': self._avg(av),
            'h2h_avg':  self._avg(xv),
            'samples': {'home': len(hv), 'away': len(av), 'h2h': len(xv)},
        }

    def _analyze_goals(self, home, away, h2h):
        hv = self._vals(home, 'total_goals')
        av = self._vals(away, 'total_goals')
        xv = self._vals(h2h,  'total_goals')
        all_v = hv + av + xv
        if len(all_v) < MIN_MATCHES:
            return None

        avg = sum(all_v) / len(all_v)

        xg_vals = self._vals(home, 'total_xg') + self._vals(away, 'total_xg')
        xg_avg  = None
        if len(xg_vals) >= 4:
            xg_avg = round(sum(xg_vals) / len(xg_vals), 1)
            avg = avg * 0.65 + xg_avg * 0.35

        line = self._best_line(all_v, LINES['goals'], 2.5)
        side = 'under' if avg < line else 'over'
        conf = self._weighted_confidence(hv, av, xv, line, side)

        if conf is None or conf < 58:
            return None
        return {
            'projected': round(avg, 1),
            'xg_avg': xg_avg,
            'line': line, 'side': side, 'confidence': conf,
            'home_avg': self._avg(hv),
            'away_avg': self._avg(av),
            'h2h_avg':  self._avg(xv),
            'samples': {'home': len(hv), 'away': len(av), 'h2h': len(xv)},
        }

    def _analyze_yellow_cards(self, home, away, h2h, ref_stats):
        hv = self._vals(home, 'total_yellows')
        av = self._vals(away, 'total_yellows')
        xv = self._vals(h2h,  'total_yellows')
        all_v = hv + av + xv

        has_team_data = len(all_v) >= MIN_MATCHES
        has_ref_data  = ref_stats is not None

        if not has_team_data and not has_ref_data:
            return None

        team_avg = sum(all_v) / len(all_v) if all_v else None

        if has_ref_data:
            ref_avg = ref_stats['avg_yellows']
            ref_w   = min(0.50, ref_stats['sample_size'] / 20)
            team_w  = 1 - ref_w
            avg = (team_avg * team_w + ref_avg * ref_w) if team_avg is not None else ref_avg
        else:
            avg     = team_avg
            ref_avg = None

        line = self._best_line(all_v if all_v else [avg], LINES['yellow_cards'], 3.5)
        side = 'under' if avg < line else 'over'

        conf = self._weighted_confidence(hv, av, xv, line, side) if has_team_data else None

        if has_ref_data:
            ref_side = 'under' if ref_stats['avg_yellows'] < line else 'over'
            if conf is not None:
                if ref_side == side:
                    conf = min(90, conf + 8)
            else:
                conf = min(75, ref_stats['sample_size'] * 5)
                side = ref_side

        if conf is None or conf < 58:
            return None
        return {
            'projected': round(avg, 1),
            'line': line, 'side': side, 'confidence': conf,
            'referee_avg': ref_stats['avg_yellows'] if has_ref_data else None,
            'referee_sample': ref_stats['sample_size'] if has_ref_data else None,
            'home_avg': self._avg(hv),
            'away_avg': self._avg(av),
            'samples': {
                'home': len(hv), 'away': len(av), 'h2h': len(xv),
                'referee': ref_stats['sample_size'] if has_ref_data else 0,
            },
        }

    def _analyze_btts(self, home, away, h2h):
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

    def _analyze_fixture(self, fixture, home, away, h2h, ref_stats):
        home_id = fixture.home_team_id
        away_id = fixture.away_team_id
        hn      = fixture.home_team_name
        an      = fixture.away_team_name

        markets = {}
        for key, fn in [
            ('corners',      lambda: self._analyze_corners(home, away, h2h)),
            ('goals',        lambda: self._analyze_goals(home, away, h2h)),
            ('yellow_cards', lambda: self._analyze_yellow_cards(home, away, h2h, ref_stats)),
            ('btts',         lambda: self._analyze_btts(home, away, h2h)),
        ]:
            result = fn()
            if result:
                markets[key] = result

        top_picks = self._build_top_picks(markets, hn, an)

        return {
            'fixture_id':  fixture.id,
            'home_team':   {'id': home_id, 'name': hn},
            'away_team':   {'id': away_id, 'name': an},
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
