from fastapi import APIRouter, Path, Query
from services.baseball_service import BaseballService

router = APIRouter(prefix="/baseball", tags=["baseball"])


@router.get("/schedule")
def get_schedule(
    date: str = Query(..., description="Date in YYYY-MM-DD format"),
    league: str = Query("lmb", description="League: lmb or mlb"),
):
    return BaseballService().get_schedule(date, league)


@router.get("/boxscore/{game_pk}")
def get_boxscore(
    game_pk: int = Path(..., description="MiLB Stats API gamePk"),
):
    return BaseballService().get_boxscore(game_pk)


@router.get("/pitcher-stats/{person_id}")
def get_pitcher_stats(
    person_id: int = Path(..., description="MLB Stats API person id"),
    league: str = Query("lmb", description="League: lmb or mlb — determines the sportId used for the stats lookup"),
):
    return BaseballService().get_pitcher_stats(person_id, league)


@router.get("/pitcher-gamelog/{person_id}")
def get_pitcher_gamelog(
    person_id: int = Path(..., description="MLB Stats API person id"),
    league: str = Query("lmb", description="League: lmb or mlb — determines the sportId used for the lookup"),
    seasons: int = Query(5, description="How many seasons back to include (this year + seasons-1 prior)"),
):
    return BaseballService().get_pitcher_game_log(person_id, league, seasons)
