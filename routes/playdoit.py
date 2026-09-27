from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from services.playdoit_import_service import PlaydoitImportService
from utils.database import get_db

router = APIRouter(prefix="/playdoit", tags=["playdoit"])


@router.post("/import")
async def trigger_import_playdoit(token: str, date_from: str, date_to: str,
                                  dry_run: bool = False, db: Session = Depends(get_db)):
  """
  Imports tickets from Playdoit (Altenar) for a date range, replacing
  manual capture via the Telegram bot.

  `token` is the Playdoit session JWT WITHOUT the "Bearer " prefix (the
  client adds it) -- short-lived (hours), copied fresh from the browser
  inspector on each run, never stored.
  `date_from`/`date_to` are ISO UTC, same format the widget itself sends
  (e.g. "2026-09-25T06:00:00.000Z").
  `dry_run=true` maps and returns the tickets without saving them -- to
  review the result before writing to the DB.
  """
  service = PlaydoitImportService(db, token)
  return service.import_range(date_from, date_to, dry_run=dry_run)
