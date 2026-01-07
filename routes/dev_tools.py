import os
import subprocess
from fastapi import APIRouter, HTTPException, BackgroundTasks
from dotenv import load_dotenv

load_dotenv()

router = APIRouter(prefix="/dev-tools", tags=["dev-tools"])

def run_sync_process(tables: list[str] = None):
  dump_path = os.getenv("PG_DUMP_PATH")
  psql_url = os.getenv("PSQL_PATH")
  source_url = os.getenv("RAILWAY_DB_URL")
  target_url = os.getenv("DATABASE_URL")
  temp_file = "temp_dump.sql"

  try:
    # Dump the source database to a temporary file
    with open(temp_file, "w") as f:
      dump_args = [dump_path, "--dbname", source_url, "-cO"]
      if tables:
        for t in tables:
          dump_args.extend(["-t", t])

      subprocess.run(dump_args, check=True, stdout=f)
      
      # Restore the dump to the target database
      with open(temp_file, "r") as f:
        restore_args = [psql_url, "--dbname", target_url]
        subprocess.run(restore_args, check=True, stdin=f)

        print("Database sync completed successfully.")
  except Exception as e:
    print(f"Database sync failed: {e}")
  finally:
    if os.path.exists(temp_file):
      os.remove(temp_file)

@router.post("/sync-db")
async def trigger_sync(background_tasks: BackgroundTasks, tables: list[str] = None):
  """
  Trigger database synchronization in the background.
  Optionally specify tables to sync.
  """
  if os.getenv("APP_ENV") != "localhost":
    raise HTTPException(status_code=403, detail="Database sync is only allowed in localhost environment.")
  
  background_tasks.add_task(run_sync_process, tables)
  return {"message": "Database sync initiated in the background."}



    
    