from fastapi import APIRouter, Depends
from pydantic import BaseModel
from sqlalchemy.orm import Session

from services.playdoit_import_service import PlaydoitImportService
from utils.database import get_db

router = APIRouter(prefix="/playdoit", tags=["playdoit"])


class PendingRequest(BaseModel):
  bet_ids: list[int]


class ImportRequest(BaseModel):
  bets: list[dict]
  dry_run: bool = False


@router.post("/pending")
def pending_playdoit_bets(body: PendingRequest, db: Session = Depends(get_db)):
  """
  IDs (from the browser's Playdoit bet history) whose detail still has to be
  fetched: new bets and saved ones that aren't final yet.
  """
  return {"pending": PlaydoitImportService(db).pending_ids(body.bet_ids)}


@router.post("/import")
def import_playdoit_bets(body: ImportRequest, db: Session = Depends(get_db)):
  """
  Maps and saves raw Playdoit bet details fetched by the browser (Cloudflare
  blocks Playdoit's API from Railway). `dry_run=true` maps and returns the
  tickets without saving them.
  """
  return PlaydoitImportService(db).import_bets(body.bets, dry_run=body.dry_run)
