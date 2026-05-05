import asyncio
import logging
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from tasks.prewarm_h2h import PrewarmCacheWorker
from tasks.live_matches import LiveWorker
from tasks.prewarm_recent_matches import PrewarmRecentMatchesWorker
from dotenv import load_dotenv
import os

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logging.getLogger('apscheduler').setLevel(logging.WARNING)
logger = logging.getLogger('Orchestrator')

load_dotenv()
minutes_interval = int(os.getenv("WORKER_INTERVAL_MINUTES", 5))

prewarm_worker = PrewarmCacheWorker()
recent_matches_worker = PrewarmRecentMatchesWorker()


async def run_nightly_pipeline():
    """
    Sequential pre-warm pipeline: Matches → H2H → Recent Matches.
    Each task awaits completion before the next one starts.
    asyncio.to_thread() is used because the underlying workers are synchronous.
    """
    logger.info("🌙 Nightly pipeline starting...")
    await asyncio.to_thread(prewarm_worker.prewarm_match_schedules)
    await asyncio.to_thread(prewarm_worker.prewarm_h2h_data)
    await asyncio.to_thread(recent_matches_worker.prewarm_recent_matches)
    logger.info("🌙 Nightly pipeline complete.")


async def main():
    live_worker = LiveWorker()

    scheduler = AsyncIOScheduler(job_defaults={'max_instances': 1})

    # ==========================================
    # TASK 1-3: Nightly Pre-warm Pipeline (00:15)
    # ==========================================
    # Runs the three pre-warm jobs sequentially in a single pipeline.
    # Replaces three separate cron jobs (00:30, 00:45, 01:00).
    scheduler.add_job(
        run_nightly_pipeline,
        CronTrigger(hour=0, minute=15, timezone='America/Mexico_City'),
        id='nightly_pipeline',
        name='Nightly Pre-warm Pipeline',
        replace_existing=True
    )

    # ==========================================
    # TASK 4: Live Window Calculator (01:15)
    # ==========================================
    scheduler.add_job(
        live_worker.calculate_live_windows,
        CronTrigger(hour=1, minute=15, timezone='America/Mexico_City'),
        id='window_calculator_worker',
        name='Window Calculator Worker',
        replace_existing=True
    )

    # ==========================================
    # TASK 5: Live Match Updater (interval)
    # ==========================================
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
