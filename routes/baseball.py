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
