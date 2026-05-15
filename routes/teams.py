from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import or_

from utils import database
from models.fixture import Fixture
from models.fixture_team_stats import FixtureTeamStats
from models.league import League
from utils.fixture_serializer import serialize_fixture

router = APIRouter(prefix="/teams", tags=["teams"])

FINISHED_STATUSES = {"FT", "AET", "PEN"}


@router.get("/{team_id}/recent-matches")
def get_team_recent_matches(team_id: int, db: Session = Depends(database.get_db)):
    fixtures = (
        db.query(Fixture)
        .filter(
            or_(Fixture.home_team_id == team_id, Fixture.away_team_id == team_id),
            Fixture.status.in_(FINISHED_STATUSES),
        )
        .order_by(Fixture.date_utc.desc())
        .limit(5)
        .all()
    )

    league_ids = {f.league_id for f in fixtures if f.league_id}
    leagues = {
        l.id: l.name
        for l in db.query(League).filter(League.id.in_(league_ids)).all()
    }

    result = []
    for fixture in fixtures:
        stats = (
            db.query(FixtureTeamStats)
            .filter(FixtureTeamStats.fixture_id == fixture.id)
            .all()
        )
        result.append(serialize_fixture(fixture, stats, leagues.get(fixture.league_id)))

    return {"team_id": team_id, "data": result}
