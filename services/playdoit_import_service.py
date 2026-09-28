import json
from datetime import datetime

from models.league import League
from services.bet_service import BetService
from services.playdoit_client import PlaydoitClient
from utils.odds import normalize_odds


def _map_or_warn(code, mapping: dict, default, label: str):
    """Looks up `code` in `mapping`. Logs a warning and returns `default`
    instead of guessing when `code` isn't in the map yet."""
    if code not in mapping:
        print(f"[PLAYDOIT] unknown {label}: {code!r} -> using {default!r} as default")
        return default
    return mapping[code]


_SPORT_MAP = {
    76: 'baseball',
    66: 'futbol',
    75: 'american_football',
}
# No fallback guess here on purpose: showing 'unknown' beats picking the
# wrong sport. Add new sport ids to the map above as they show up.
_UNKNOWN_SPORT = 'unknown'

# Altenar bet.status -> our status. Confirmed with 5 real tickets checked
# by hand: 0=open, 1=won, 2=lost, 8=push, 18="closed" (same payout pattern
# as push, treated the same). Unknown codes fall back to 'pending'.
_STATUS_MAP = {
    0: 'pending',
    1: 'won',
    2: 'lost',
    8: 'push',
    18: 'push',
}
_FINAL_STATUSES = ('won', 'lost', 'push')

# Altenar bet.device -> device_type. Confirmed with 2 real tickets.
_DEVICE_MAP = {
    0: 'desktop',
    1: 'mobile',
}


# Altenar bet.type codes confirmed with real tickets so far: 0=simple,
# 1=combo (used for BOTH cross-game parlays and single-selection
# BetBuilders -- Altenar doesn't tell them apart, we do via isBetBuilder
# below), 3=teaser (confirmed: same-game handicap+total, totalOdds LOWER
# than price1*price2, a real line adjustment).
_BET_TYPE_TEASER = 3


def _classify_bet_type(bet_type_code, selections: list) -> str:
    """simple / crear_apuesta / parlay / teaser, based only on raw Playdoit
    data (see the bet.type codes above and `isBetBuilder` per selection)."""
    if bet_type_code == _BET_TYPE_TEASER:
        return 'teaser'
    if len(selections) > 1:
        return 'parlay'
    if selections and selections[0].get('isBetBuilder'):
        return 'crear_apuesta'
    return 'simple'


def _parse_side(name: str) -> str | None:
    n = (name or '').strip().lower()
    if n.startswith('más de') or n.startswith('mas de'):
        return 'over'
    if n.startswith('menos de'):
        return 'under'
    if n in ('sí', 'si'):
        return 'yes'
    if n == 'no':
        return 'no'
    return None


def _parse_line(spec: str | None) -> float | None:
    if not spec:
        return None
    try:
        values = json.loads(spec)
        raw = next(iter(values.values()), None)
        return float(raw) if raw is not None else None
    except (ValueError, TypeError, StopIteration):
        return None


def _parse_dt(iso: str | None) -> datetime | None:
    if not iso:
        return None
    try:
        return datetime.fromisoformat(iso.replace('Z', '+00:00'))
    except ValueError:
        return None


def _leg_outcome(sel_status_code) -> bool | None:
    """True/False only when this specific pick already won or lost.
    pending/push have no clear binary answer, so they stay None on purpose."""
    sel_status = _map_or_warn(sel_status_code, _STATUS_MAP, 'pending', 'selection.status')
    if sel_status == 'won':
        return True
    if sel_status == 'lost':
        return False
    return None


def _build_leg(sel: dict, champ_catalog: dict) -> dict:
    side = _parse_side(sel.get('name'))
    # `spec` doesn't always hold a numeric line -- for 1x2/moneyline markets
    # it's just the selection index (e.g. {"8":"1"}), not an over/under.
    # Only trust it when the pick is actually over/under.
    line_used = _parse_line(sel.get('spec')) if side in ('over', 'under') else None
    champ_id = sel.get('champId')
    return {
        'match_name': sel.get('eventName'),
        # Resolved from the `leagues` catalog (by playdoit_champ_id) if
        # already linked; otherwise stays None -- the frontend has a
        # dropdown to link it once, we don't guess it here.
        'league': champ_catalog.get(champ_id),
        'pick': sel.get('name'),
        # Not mapped to our own market canon on purpose -- market_type_id is
        # Altenar's raw id, the source of truth. Guessing a canon value here
        # (with 'other' as a catch-all) would bring back the same guesswork
        # the Telegram bot's vision model had. If a canon name is ever
        # needed, resolve it separately without losing the raw value.
        'market': None,
        'market_type_id': sel.get('marketTypeId'),
        'market_name_raw': sel.get('marketName'),
        'champ_id': champ_id,
        'is_bet_builder': bool(sel.get('isBetBuilder')),
        'side': side,
        'line_used': line_used,
        'odd': normalize_odds(sel.get('price')),
        'outcome': _leg_outcome(sel.get('status')),
        'event_date': sel.get('eventDate'),
    }


class PlaydoitImportService:
    """
    Imports tickets from Playdoit (Altenar), replacing manual capture via
    the Telegram bot + vision model. The token is a short-lived session
    token (hours) -- passed fresh on every run, never stored.

    `league` is resolved from the `leagues` catalog (by `playdoit_champ_id`)
    when already linked; otherwise it stays `None` until it's linked once
    from the frontend (dropdown on the ticket detail).
    """
    def __init__(self, db, token: str):
        self.db = db
        self.bet_service = BetService(db)
        self.client = PlaydoitClient(token)

    def _load_champ_catalog(self) -> dict:
        """{playdoit_champ_id: league name}, ONE query per import run --
        never a query per ticket (same pattern as `venue_tz` in MLB Radar)."""
        rows = (self.db.query(League.playdoit_champ_id, League.name)
                       .filter(League.playdoit_champ_id.isnot(None)).all())
        return {champ_id: name for champ_id, name in rows}

    def import_range(self, date_from: str, date_to: str, dry_run: bool = False) -> dict:
        """
        Date ranges can overlap between runs (e.g. running daily with a
        "last N days" window), so every existing ticket is re-checked by
        status instead of just being skipped:
          - already final (won/lost/push) -> skip, it won't change anymore.
          - still pending -> re-fetch the detail and update it, in case it
            got resolved since the last run.
          - doesn't exist yet -> import it as usual.
        """
        champ_catalog = self._load_champ_catalog()
        bets = self.client.get_all_bets(date_from, date_to, statuses=None)
        imported, updated, skipped, failed, previewed = [], [], [], [], []

        for bet_summary in bets:
            ticket_id = f"playdoit:{bet_summary['id']}"
            existing = self.bet_service.get_ticket_by_id(ticket_id)
            if existing and existing.status in _FINAL_STATUSES:
                skipped.append(ticket_id)
                continue

            detail = self.client.get_bet_detail(bet_summary['id'])
            if not detail or not detail.get('selections'):
                failed.append(ticket_id)
                continue

            ticket_data = self._map_ticket(ticket_id, detail, champ_catalog)
            if dry_run:
                previewed.append(ticket_data)
            elif existing:
                self.bet_service.update_ticket(ticket_id, ticket_data)
                updated.append(ticket_id)
            else:
                self.bet_service.create_ticket(ticket_data)
                imported.append(ticket_id)

        result = {
            'dry_run': dry_run,
            'date_from': date_from, 'date_to': date_to,
            'found': len(bets), 'imported': len(imported), 'updated': len(updated),
            'skipped_final': len(skipped), 'failed': len(failed),
            'imported_ids': imported, 'updated_ids': updated, 'failed_ids': failed,
        }
        if dry_run:
            result['preview'] = previewed
        return result

    def _map_ticket(self, ticket_id: str, bet: dict, champ_catalog: dict) -> dict:
        selections = bet['selections']
        legs = [_build_leg(s, champ_catalog) for s in selections]

        bet_type = _classify_bet_type(bet.get('type'), selections)

        # Only set if every leg agrees on one league -- forcing a single
        # league onto a multi-league bet would skew per-league stats.
        leagues_resolved = list(dict.fromkeys(l['league'] for l in legs if l.get('league')))
        league = leagues_resolved[0] if len(leagues_resolved) == 1 else None

        event_names = list(dict.fromkeys(s.get('eventName') for s in selections if s.get('eventName')))
        event_dates = sorted(d for d in (s.get('eventDate') for s in selections) if d)
        match_datetime = _parse_dt(event_dates[0] if event_dates else bet.get('createdDate'))

        status = _map_or_warn(bet.get('status'), _STATUS_MAP, 'pending', 'bet.status')
        stake, payout = bet.get('totalStake'), bet.get('totalWin')
        net_profit = (payout - stake) if status != 'pending' and stake is not None and payout is not None else None

        return {
            'ticket_id': ticket_id,
            'sport': _map_or_warn(selections[0].get('sportId'), _SPORT_MAP, _UNKNOWN_SPORT, 'sportId'),
            'league': league,
            'pick': ' + '.join(l['pick'] for l in legs if l.get('pick')),
            'odds': normalize_odds(bet.get('totalOdds')),
            'stake': stake,
            'payout': payout,
            'net_profit': net_profit,
            'status': status,
            'match_name': ' + '.join(event_names) if event_names else None,
            'bet_type': bet_type,
            'match_datetime': match_datetime,
            'device_type': _DEVICE_MAP.get(bet.get('device')),
            'studied': False,
            'comments': 'Importado de Playdoit',
            'image_path': None,
            'legs': legs,
        }
