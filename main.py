from typing import List, Optional
from fastapi import FastAPI, Query
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
def get_leagues(id: Optional[List[int]] = Query(None)):
    db = database.SessionLocal()
    try:
        query = db.query(models.League).order_by(models.League.id)
        if id:
            query = query.filter(models.League.id.in_(id))
        leagues = query.all()
        return leagues
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()