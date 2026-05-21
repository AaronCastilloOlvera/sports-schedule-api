import re

FRIENDLIES_LEAGUE_ID = 10
_YOUTH_PATTERN = re.compile(r'\bU\d{2}\b|\bUnder[ -]?\d{2}\b|\bYouth\b|\bReserve', re.IGNORECASE)


def is_youth_match(match: dict) -> bool:
    if match.get("league", {}).get("id") != FRIENDLIES_LEAGUE_ID:
        return False
    home_name = match.get("teams", {}).get("home", {}).get("name", "")
    away_name = match.get("teams", {}).get("away", {}).get("name", "")
    return any(_YOUTH_PATTERN.search(name) for name in (home_name, away_name))
