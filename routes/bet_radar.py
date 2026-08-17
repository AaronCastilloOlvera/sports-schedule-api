import json
from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy.orm import Session
from utils.database import get_db
from utils.redis_client import get_redis_connection
from services.bet_radar_service import BetRadarService

router = APIRouter(prefix="/bet-radar", tags=["BetRadar"])


@router.get("/suggestions")
def get_bet_radar_suggestions(
    date: str = Query(..., description="YYYY-MM-DD — fecha de los partidos a analizar"),
    db: Session = Depends(get_db),
):
    return BetRadarService(db).get_suggestions(date)


@router.get("/cached")
def get_cached_bet_radar(
    date: str = Query(..., description="YYYY-MM-DD"),
):
    """Lee sugerencias pre-computadas del pipeline nocturno desde Redis."""
    r, error = get_redis_connection()
    if r is None:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {error}")
    raw = r.get(f"bet_radar:{date}")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No hay BetRadar cacheado para {date}.")
    return json.loads(raw)
