import logging
from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger
from datetime import datetime, timedelta
from tasks.prewarn_h2h import prewarm_matches 

logging.basicConfig(level=logging.INFO, format='[%(asctime)s] %(levelname)s - %(message)s')
logging = logging.getLogger('Orchestrator')

def start_orchestrator():
  logging.info("🚀 Starting Orchestrator...")
  scheduler = BlockingScheduler(job_defaults={'max_instances': 1})

  # ========================
  # TASK 1: Nightly Scout 
  # Works at 3:00 AM, when there are no matches being played.
  # ========================
  scheduler.add_job(
    prewarm_matches,
    CronTrigger(hour=3, minute=0, timezone='America/Mexico_City'),
    id='h2h_prewarm_worker',
    name='H2H Cache Prewarming Worker',
    replace_existing=True
  )

  try:
    scheduler.start()
  except (KeyboardInterrupt, SystemExit):
    logging.info("Shutting down Orchestrator...")

if __name__ == "__main__":
  start_orchestrator()  