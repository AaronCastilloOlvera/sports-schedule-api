from models.fixture import Fixture

TEAM_LOGO_BASE = "https://media.api-sports.io/football/teams"


def serialize_fixture(fixture: Fixture, team_stats: list, league_name: str = None) -> dict:
    hg = fixture.home_goals
    ag = fixture.away_goals
    if hg is not None and ag is not None:
        home_winner = hg > ag
        away_winner = ag > hg
        if hg == ag:
            home_winner = False
            away_winner = False
    else:
        home_winner = None
        away_winner = None

    return {
        "fixture": {
            "id": fixture.id,
            "date": fixture.date_utc.isoformat() if fixture.date_utc else None,
            "status": {"short": fixture.status},
            "venue": {"name": fixture.venue_name},
            "referee": fixture.referee,
        },
        "league": {
            "id": fixture.league_id,
            "name": league_name,
            "season": fixture.season,
        },
        "teams": {
            "home": {
                "id": fixture.home_team_id,
                "name": fixture.home_team_name,
                "logo": f"{TEAM_LOGO_BASE}/{fixture.home_team_id}.png",
                "winner": home_winner,
            },
            "away": {
                "id": fixture.away_team_id,
                "name": fixture.away_team_name,
                "logo": f"{TEAM_LOGO_BASE}/{fixture.away_team_id}.png",
                "winner": away_winner,
            },
        },
        "goals": {"home": fixture.home_goals, "away": fixture.away_goals},
        "score": {
            "halftime": {"home": fixture.home_goals_ht, "away": fixture.away_goals_ht},
            "fulltime": {"home": fixture.home_goals, "away": fixture.away_goals},
        },
        "statistics": _serialize_team_stats(team_stats),
    }


def _serialize_team_stats(stats_rows: list) -> list:
    result = []
    for s in stats_rows:
        result.append({
            "team": {"id": s.team_id, "name": s.team_name},
            "statistics": [
                {"type": "Total Shots", "value": s.shots_total},
                {"type": "Shots on Goal", "value": s.shots_on_target},
                {"type": "Shots off Goal", "value": s.shots_off_target},
                {"type": "Blocked Shots", "value": s.shots_blocked},
                {"type": "Shots insidebox", "value": s.shots_inside_box},
                {"type": "Shots outsidebox", "value": s.shots_outside_box},
                {"type": "Ball Possession", "value": f"{s.possession}%" if s.possession is not None else None},
                {"type": "Total passes", "value": s.passes_total},
                {"type": "Passes accurate", "value": s.passes_accurate},
                {"type": "Passes %", "value": f"{s.passes_accuracy}%" if s.passes_accuracy is not None else None},
                {"type": "Fouls", "value": s.fouls},
                {"type": "Corner Kicks", "value": s.corners},
                {"type": "Offsides", "value": s.offsides},
                {"type": "Yellow Cards", "value": s.yellow_cards},
                {"type": "Red Cards", "value": s.red_cards},
                {"type": "Goalkeeper Saves", "value": s.saves},
                {"type": "expected_goals", "value": str(s.expected_goals) if s.expected_goals is not None else None},
            ],
        })
    return result
