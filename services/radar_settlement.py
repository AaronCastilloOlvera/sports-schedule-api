"""
Pick grading for the ESPN-sourced radars (NFL, NBA, MLB) -- the counterpart
of `bet_radar_service.settle_pick` for soccer. `settle_game_pick` is the single
source of truth: each radar's `_evaluate_pick` (accuracy) and the live
`/results` endpoints both go through it, so they can never disagree.

`game` is each service's `compact_game()` output: home/away `score`, plus
`hits`, `first_inning_runs` and `innings_played` for MLB.
"""

NFL_NBA_PENDING = {'STATUS_SCHEDULED'}
NFL_NBA_VOID = {'STATUS_POSTPONED', 'STATUS_CANCELED'}
MLB_PENDING = {'Scheduled', 'Pre-Game', 'Warmup'}
MLB_VOID = {'Postponed', 'Canceled', 'Cancelled'}
MLB_FINAL = {'Final', 'Game Over', 'Completed Early'}


def _by_sign(value, final):
    """won/lost/push once final; winning/losing/even while still in play."""
    if value > 0:
        return 'won' if final else 'winning'
    if value < 0:
        return 'lost' if final else 'losing'
    return 'push' if final else 'even'


def settle_game_pick(market, side, line, game, final):
    """
    state: won | lost | push | winning | losing | even | unknown.
    Over/under can be decided before the end (an over past its line can't
    lose; an under that broke it can't win), same as soccer. NRFI is decided
    as soon as the 1st inning gives up a run, or once it ends scoreless.
    actual/margin feed the "won by X" text: for spread, actual is the picked
    team's point margin and margin is how far it covered (+) or missed (-).
    """
    hs = game['home'].get('score') or 0
    as_ = game['away'].get('score') or 0
    unknown = {'state': 'unknown', 'actual': None, 'margin': None}

    if market == 'moneyline':
        lead = hs - as_ if side == 'home' else as_ - hs
        return {'state': _by_sign(lead, final), 'actual': lead, 'margin': lead}

    if market == 'spread':
        if line is None:
            return unknown
        lead = hs - as_ if side == 'home' else as_ - hs
        cover = lead + line
        return {'state': _by_sign(cover, final), 'actual': lead, 'margin': cover}

    if market in ('total', 'hits'):
        if market == 'total':
            actual = hs + as_
        else:
            h, a = game['home'].get('hits'), game['away'].get('hits')
            actual = None if h is None or a is None else h + a
        if actual is None or line is None:
            return unknown
        if final and actual == line:
            state = 'push'
        elif side == 'over':
            state = 'won' if actual > line else ('lost' if final else 'losing')
        else:
            state = 'lost' if actual > line else ('won' if final else 'winning')
        return {'state': state, 'actual': actual, 'margin': actual - line}

    if market == 'nrfi':
        fh, fa = game['home'].get('first_inning_runs'), game['away'].get('first_inning_runs')
        if final and (fh is None or fa is None):
            return unknown
        runs = (fh or 0) + (fa or 0)
        first_done = final or (game.get('innings_played') or 0) >= 2
        # side 'yes' = NRFI (no run in the 1st), 'no' = YRFI
        if runs > 0:
            state = 'won' if side == 'no' else 'lost'
        elif first_done:
            state = 'won' if side == 'yes' else 'lost'
        else:
            state = 'winning' if side == 'yes' else 'losing'
        return {'state': state, 'actual': runs, 'margin': None}

    return unknown


def evaluate_final(pick, game):
    """Accuracy adapter: 'win' | 'loss' | None (push, missing data)."""
    result = settle_game_pick(pick['market'], pick['side'], pick.get('line'), game, final=True)
    return {'won': 'win', 'lost': 'loss'}.get(result['state'])


def nfl_nba_phase(game):
    """(pending|live|final|void, clock label) from an NFL/NBA compact game."""
    state = game.get('state')
    if state in NFL_NBA_VOID:
        return 'void', None
    if game.get('completed'):
        return 'final', None
    if state in NFL_NBA_PENDING:
        return 'pending', None
    if state == 'STATUS_HALFTIME':
        return 'live', 'Medio tiempo'
    period = game.get('period') or 0
    quarter = f'Q{period}' if period <= 4 else 'OT'
    return 'live', f"{quarter} {game.get('display_clock') or ''}".strip()


def mlb_phase(game):
    """(pending|live|final|void, inning label) from an MLB compact game."""
    state = game.get('state')
    if state in MLB_FINAL:
        return 'final', None
    if state in MLB_VOID:
        return 'void', None
    if state in MLB_PENDING:
        return 'pending', None
    return 'live', f"{game.get('innings_played') or 1}ª entrada"


def grade_picks(suggestions, games_by_id, id_key, phase_of):
    """
    {game_id: entry} for every suggestion whose game already started — same
    shape as soccer's `bet_radar:results:*` so the frontend renders all sports
    the same way. Not-started games are left out on purpose.
    """
    results = {}
    for s in suggestions:
        game = games_by_id.get(str(s.get(id_key)))
        if not game or not s.get('top_picks'):
            continue
        phase, clock = phase_of(game)
        if phase == 'pending':
            continue
        picks = []
        for p in s['top_picks']:
            base = {'market': p['market'], 'side': p['side'], 'line': p.get('line')}
            if phase == 'void':
                picks.append({**base, 'state': 'void', 'actual': None, 'margin': None})
            else:
                picks.append({**base, **settle_game_pick(p['market'], p['side'], p.get('line'),
                                                         game, final=phase == 'final')})
        results[str(s[id_key])] = {
            'status': phase,
            'clock': clock,
            'home_score': game['home'].get('score'),
            'away_score': game['away'].get('score'),
            'final': phase in ('final', 'void'),
            'picks': picks,
        }
    return results
