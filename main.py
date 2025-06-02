from typing import List, Optional
from fastapi import FastAPI
import models, database

app = FastAPI()
models.Base.metadata.create_all(bind=database.engine)

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.get("/ping-db")    
def ping_db():
    db = database.SessionLocal()
    try:
        yield db
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()

@app.get("/leagues")
def get_leagues(id: Optional[int] = None) -> List[models.League]:
    db = database.SessionLocal()
    try:
        if id:
            query = db.query(models.League.id.in_(id))
        leagues = query.all()
        return leagues
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()