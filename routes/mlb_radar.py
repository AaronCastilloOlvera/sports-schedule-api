import json

from fastapi import APIRouter, HTTPException, Query

from services.mlb_radar_service import MLBRadarService
from utils.redis_client import get_redis_connection

router = APIRouter(prefix="/mlb-radar", tags=["MLBRadar"])


def _redis():
    r, error = get_redis_connection()
    if r is None:
        raise HTTPException(status_code=503, detail=f"Redis unavailable: {error}")
    return r


@router.get("/suggestions")
def get_mlb_radar_suggestions(
    date: str = Query(..., description="YYYY-MM-DD — fecha de los juegos a analizar"),
):
    """Calcula los picks en caliente (refresca caché diaria y perfiles si hace falta)."""
    r, _ = get_redis_connection()
    return MLBRadarService(r).get_suggestions(date)


@router.get("/cached")
def get_cached_mlb_radar(
    date: str = Query(..., description="YYYY-MM-DD"),
):
    """Lee los picks pre-computados por el pipeline nocturno desde Redis."""
    raw = _redis().get(f"mlb_radar:{date}")
    if raw is None:
        raise HTTPException(status_code=404, detail=f"No hay MLB Radar cacheado para {date}.")
    return json.loads(raw)


@router.get("/accuracy")
def get_mlb_radar_accuracy(
    days: int = Query(7, ge=1, le=30, description="Ventana de días hacia atrás (excluye hoy)"),
    min_confidence: int = Query(70, ge=0, le=100, description="Confianza mínima del pick"),
    date: str = Query(None, description="YYYY-MM-DD — fecha final de la ventana (default: hoy)"),
):
    """Efectividad real: picks cacheados vs resultados reales (caché diaria), incluye ROI real."""
    r = _redis()
    return MLBRadarService(r).get_accuracy(
        r, days=days, min_confidence=min_confidence, end_date=date
    )
