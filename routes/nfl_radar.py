import json

from fastapi import APIRouter, HTTPException, Query

from services.nfl_radar_service import NFLRadarService
from utils.redis_client import get_redis_connection

router = APIRouter(prefix="/nfl-radar", tags=["NFLRadar"])


def _redis():
    r, error = get_redis_connection()
    if r is None:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {error}")
    return r


@router.get("/schedule")
def get_nfl_schedule(
    date: str = Query(..., description="YYYY-MM-DD"),
):
    """Calendario del día (equipos, marcador, estado) para la vista de Partidos — TTL corto, sin odds/boxscore."""
    r, _ = get_redis_connection()
    return {"data": NFLRadarService(r).get_schedule(date)}


@router.get("/suggestions")
def get_nfl_radar_suggestions(
    date: str = Query(..., description="YYYY-MM-DD — fecha de los juegos a analizar"),
):
    """Calcula los picks en caliente (refresca caché diaria si hace falta)."""
    r, _ = get_redis_connection()
    return NFLRadarService(r).get_suggestions(date)


@router.get("/cached")
def get_cached_nfl_radar(
    date: str = Query(..., description="YYYY-MM-DD"),
):
    """Lee los picks pre-computados por el pipeline nocturno desde Redis."""
    raw = _redis().get(f"nfl_radar:{date}")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No hay NFL Radar cacheado para {date}.")
    return json.loads(raw)


@router.get("/accuracy")
def get_nfl_radar_accuracy(
    days: int = Query(7, ge=1, le=60, description="Ventana de días hacia atrás (excluye hoy)"),
    min_confidence: int = Query(70, ge=0, le=100, description="Confianza mínima del pick"),
    date: str = Query(None, description="YYYY-MM-DD — fecha final de la ventana (default: hoy)"),
):
    """Hit rate + ROI real: picks cacheados vs resultados reales, momio decimal real por pick."""
    r = _redis()
    return NFLRadarService(r).get_accuracy(r, days=days, min_confidence=min_confidence, end_date=date)
