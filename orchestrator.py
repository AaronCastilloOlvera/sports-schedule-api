import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tasks.prewarm_h2h import PrewarmCacheWorker
from tasks.live_matches import LiveWorker
from tasks.prewarm_odds import PrewarmOddsWorker
from tasks.persist_finished_fixtures import PersistFinishedFixturesWorker
from tasks.persist_h2h_fixtures import PersistH2HFixturesWorker
from tasks.persist_recent_matches import PersistRecentMatchesWorker
from dotenv import load_dotenv
import os

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logger = logging.getLogger('Orchestrator')

load_dotenv()
minutes_interval = int(os.getenv("WORKER_INTERVAL_MINUTES", 5))

prewarm_worker = PrewarmCacheWorker()
odds_worker = PrewarmOddsWorker()
persist_worker = PersistFinishedFixturesWorker()
persist_h2h_worker = PersistH2HFixturesWorker()
persist_recent_worker = PersistRecentMatchesWorker()
live_worker = LiveWorker()


async def run_nightly_pipeline():
    """
    Full nightly pipeline (00:15 MX). Two phases:
      Phase 1 — Redis/prep: populate caches and in-memory state
      Phase 2 — DB: persist historical data using phase 1 output
    Steps run sequentially; each awaits completion before the next starts.
    """
    logger.info("🌙 Nightly pipeline starting...")

    # Phase 1 — Redis / prep
    await asyncio.to_thread(prewarm_worker.prewarm_match_schedules)
    await asyncio.to_thread(live_worker.calculate_live_windows)
    await asyncio.to_thread(odds_worker.prewarm_odds)

    # Phase 2 — DB persist
    await asyncio.to_thread(persist_recent_worker.persist_recent_matches)
    await asyncio.to_thread(persist_h2h_worker.persist_h2h_fixtures)
    await asyncio.to_thread(persist_worker.persist_finished_fixtures)

    logger.info("🌙 Nightly pipeline complete.")


async def main():
    scheduler = AsyncIOScheduler(job_defaults={'max_instances': 1})

    scheduler.add_job(
        run_nightly_pipeline,
        CronTrigger(hour=2, minute=20, timezone='America/Mexico_City'),
        id='nightly_pipeline',
        name='Nightly Pipeline',
        replace_existing=True
    )

    scheduler.add_job(
        live_worker.run_live_update,
        IntervalTrigger(minutes=minutes_interval),
        id='live_update_worker',
        name='Live Update Worker',
        replace_existing=True
    )

    logger.info("✅ Orchestrator configuration complete.")

    logger.info("Executing initial window calculation...")
    live_worker.calculate_live_windows()

    scheduler.start()

    try:
        await asyncio.Event().wait()
    except (KeyboardInterrupt, SystemExit):
        logger.info("🛑 Shutting down Orchestrator safely...")
        scheduler.shutdown()


if __name__ == "__main__":
    asyncio.run(main())
