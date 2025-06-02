from sqlalchemy import create_engine, text
from sqlalchemy.orm  import sessionmaker
from fastapi import FastAPI
from dotenv import load_dotenv
import os

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
app = FastAPI()

engine = create_engine(DATABASE_URL)
SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}

@app.get("/ping-db")    
def ping_db():
    db = SessionLocal()
    try:
        db.execute(text("SELECT 1"))
        return {"message": "Database connection is healthy!"}
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()

@app.get("leagues")
def get_leagues():
    db = SessionLocal()
    try:
        result = db.execute(text("SELECT * FROM leagues")).fetchall()
        return {"leagues": [dict(row) for row in result]}
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()