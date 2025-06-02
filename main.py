from fastapi import FastAPI
from dotenv import load_dotenv
from . import models, database

load_dotenv()

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
def get_leagues():
    db = database.SessionLocal()
    try:
        leagues = db.query(models.League).all()
        return leagues
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()