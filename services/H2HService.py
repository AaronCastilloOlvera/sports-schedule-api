from sqlalchemy.orm import Session
from sqlalchemy import or_, and_

from models.fixture import Fixture
from models.fixture_team_stats import FixtureTeamStats
from models.league import League
from utils.fixture_serializer import serialize_fixture

FINISHED_STATUSES = {"FT", "AET", "PEN"}


class H2HService:

    def __init__(self, db: Session):
        self.db = db

    def get_headtohead_matches(self, team1: int, team2: int):
        fixtures = (
            self.db.query(Fixture)
            .filter(
                or_(
                    and_(Fixture.home_team_id == team1, Fixture.away_team_id == team2),
                    and_(Fixture.home_team_id == team2, Fixture.away_team_id == team1),
                ),
                Fixture.status.in_(FINISHED_STATUSES),
            )
            .order_by(Fixture.date_utc.desc())
            .limit(10)
            .all()
        )

        league_ids = {f.league_id for f in fixtures if f.league_id}
        leagues = {
            l.id: l.name
            for l in self.db.query(League).filter(League.id.in_(league_ids)).all()
        }

        result = []
        for fixture in fixtures:
            stats = (
                self.db.query(FixtureTeamStats)
                .filter(FixtureTeamStats.fixture_id == fixture.id)
                .all()
            )
            result.append(serialize_fixture(fixture, stats, leagues.get(fixture.league_id)))

        return {"data": result}
