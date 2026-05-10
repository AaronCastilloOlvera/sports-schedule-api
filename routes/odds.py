from fastapi import APIRouter
from services.odds_service import OddsService

router = APIRouter(prefix="/odds", tags=["odds"])
odds_service = OddsService()


@router.get("/fixture/{fixture_id}")
def get_fixture_odds(fixture_id: int):
    return odds_service.get_odds_by_fixture(fixture_id)


@router.get("/fixture/{fixture_id}/markets")
def get_fixture_markets(fixture_id: int):
    markets = odds_service.get_available_markets(fixture_id)
    return {"fixture_id": fixture_id, "markets": markets}
