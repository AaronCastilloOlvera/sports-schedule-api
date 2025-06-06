from typing import List, Optional
from fastapi import FastAPI, Query
from fastapi.middleware.cors import CORSMiddleware
from dotenv import load_dotenv
import models, database
import os
import redis
import json
import requests

load_dotenv()
origins = os.getenv("ALLOWED_ORIGINS", "").split(",")

app = FastAPI()
app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Create the database tables
models.Base.metadata.create_all(bind=database.engine)

# Headers for external API requests
headers = { "x-rapidapi-host": os.getenv("API_URL"), "x-rapidapi-key": os.getenv("API_KEY") }

# Const
favorite_leagues = [ 2, 39, 61, 71, 78, 135, 140, 262]

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

@app.get("/favorite-leagues")
def favorite_league():
    db = database.SessionLocal()
    try:
        query = db.query(models.League).filter(models.League.id.in_(favorite_leagues)).order_by(models.League.id)
        return query.all()
    except Exception as e:
        return {"error": str(e)}
    finally:
        db.close()

@app.get("/leagues/matches/{date}")
def get_matches_by_date(date: str):
    try:
        r = get_redis_connection()
        if r is None: 
            return {"error": "Redis connection failed"} 
    
        cached_data  = r.get(str(date))
        if cached_data:
            return json.loads(cached_data) 

        url = f"https://{os.getenv("API_URL")}/fixtures?"
        params = {"date": date}
        response = requests.get(url, headers=headers,  params=params)
        response_data = response.json()
        matches = response_data.get("response", [])    

        filtered_data = [
            match for match in matches 
            if match.get("league", {}).get("id") in favorite_leagues
        ]

        # Store the filtered data in Redis
        r.set(date, json.dumps(filtered_data))

        return filtered_data
        
    except Exception as e:
        return {"error": "Failed to fetch matches", "details": str(e)}

@app.get("/redis/keys")
def get_redis_keys():
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}
    
    keys = r.keys("*")
    sorted_keys = sorted(keys)
    return {"keys": sorted_keys}

@app.get("/redis/data")
def get_redis_data():
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}
    
    keys = r.keys("*")
    result = {}

    for key in keys:
        value = r.get(key)
        try:
            result[key] = json.loads(value)
        except json.JSONDecodeError:
            result[key] = value
    return result

@app.delete("/redis/{key}")
def delete_redis_key(key: str):
    r = get_redis_connection()
    if r is None:
        return {"error": "Redis connection failed"}
    
    try:
        r.delete(key)
        return {"message": f"Key '{key}' deleted successfully"}
    except Exception as e:
        return {"error": str(e)}

def get_redis_connection():
    try:
        redis_url = os.getenv("REDIS_URL")
        redis_client = redis.from_url(redis_url, decode_responses=True)
        redis_client.ping()
        return redis_client
    except Exception as e:
        print(f"Redis connection failed: {e}")
        return None     