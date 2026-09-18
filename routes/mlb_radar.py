import json

from fastapi import APIRouter, HTTPException, Query

from services.mlb_radar_service import MLBRadarService
from services.mlb_api_client import LEAGUES
from utils.redis_client import get_redis_connection

router = APIRouter(prefix="/mlb-radar", tags=["MLBRadar"])


def _redis():
    r, error = get_redis_connection()
    if r is None:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {error}")
    return r


def _check_league(league: str):
    if league not in LEAGUES:
        raise HTTPException(status_code=400, detail=f"league debe ser uno de {list(LEAGUES)}")


@router.get("/suggestions")
def get_mlb_radar_suggestions(
    date: str = Query(..., description="YYYY-MM-DD — fecha de los juegos a analizar"),
    league: str = Query("mlb", description="Liga: mlb o lmb"),
):
    """Calcula los picks en caliente (refresca caché diaria y perfiles si hace falta)."""
    _check_league(league)
    r, _ = get_redis_connection()
    return MLBRadarService(r, league).get_suggestions(date)


@router.get("/cached")
def get_cached_mlb_radar(
    date: str = Query(..., description="YYYY-MM-DD"),
    league: str = Query("mlb", description="Liga: mlb o lmb"),
):
    """Lee los picks pre-computados por el pipeline nocturno desde Redis."""
    _check_league(league)
    raw = _redis().get(f"mlb_radar:{league}:{date}")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No hay MLB Radar cacheado para {league} {date}.")
    return json.loads(raw)


@router.get("/accuracy")
def get_mlb_radar_accuracy(
    days: int = Query(7, ge=1, le=30, description="Ventana de días hacia atrás (excluye hoy)"),
    min_confidence: int = Query(70, ge=0, le=100, description="Confianza mínima del pick"),
    league: str = Query("mlb", description="Liga: mlb o lmb"),
    date: str = Query(None, description="YYYY-MM-DD — fecha final de la ventana (default: hoy)"),
):
    """Efectividad real: picks cacheados vs resultados reales (caché diaria + boxscore)."""
    _check_league(league)
    r = _redis()
    return MLBRadarService(r, league).get_accuracy(
        r, days=days, min_confidence=min_confidence, league=league, end_date=date
    )
