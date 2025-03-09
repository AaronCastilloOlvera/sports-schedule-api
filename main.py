from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
import psycopg2
from dotenv import load_dotenv
import os
import http.client
import json

load_dotenv()

app = FastAPI()

origins = os.getenv("CORS_ORIGINS").split(",")

app.add_middleware(
    CORSMiddleware,
    allow_origins=origins,
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

def getConnection():
    return psycopg2.connect(
        dbname=os.getenv("DB_NAME"),
        user=os.getenv("DB_USER"),
        password=os.getenv("DB_PASSWORD"),
        host=os.getenv("DB_HOST"),
        port=os.getenv("DB_PORT")
    )

"""
class Item(BaseModel):
    title: str
    author: str
    pages: int

@app.get("/")
def index():
    return {"message": "Hello, World!"}

@app.get("/items/response")
def read_item_response():
    return JSONResponse(content={"message": "Hello, World!!!"})

@app.get("/items/{item_id}")
def read_item(item_id: int):
    return {"item_id": item_id}

@app.post("/items/")
def create_item(item: Item):
    return {"message": "Item has been created", "item": item}
"""

@app.get("/leagues")
def getLeagues(ids: str = None):
    try:
        conn = getConnection()
        cursor = conn.cursor()

        if ids:
            cursor.execute("""
                SELECT l.id, l.name, l.type, l.logo, c.name, c.code, c.flag
                FROM leagues l
                JOIN countries c ON l.country_id = c.id
                WHERE l.id IN %s
            """, (tuple(ids.split(",")),))
        else:
            cursor.execute("""
                SELECT l.id, l.name, l.type, l.logo, c.name, c.code, c.flag
                FROM leagues l
                JOIN countries c ON l.country_id = c.id
            """)
        leagues = cursor.fetchall()

        leagues_list = []
        for league in leagues:
            leagues_list.append({
                "league": {
                    "id": league[0],
                    "name": league[1],
                    "type": league[2],
                    "logo": league[3]
                },
                "country": {
                    "name": league[4],
                    "code": league[5],
                    "flag": league[6]
                }
            })

        return {"response": leagues_list}

    except Exception as e:
        print(f"Error connecting to the database: {e}")
        return {"error": str(e)}

    finally:
        cursor.close()
        conn.close()

@app.get("/favorite_leagues")
def getFavoriteLeagues():
    return getLeagues("2,4,61,71,78,88,94,129,135,140,180,262,263")

@app.get("/leagues_countries")
def getLeaguesAndCountries():
    try:
        conn = getConnection()
        cursor = conn.cursor()

        cursor.execute("""
            SELECT l.id, l.name, l.type, l.logo, c.name, c.code, c.flag
            FROM leagues l
            JOIN countries c ON l.country_id = c.id
        """)
        leagues = cursor.fetchall()

        leagues_list = []
        for league in leagues:
            leagues_list.append({
                "league": {
                    "id": league[0],
                    "name": league[1],
                    "type": league[2],
                    "logo": league[3]
                },
                "country": {
                    "name": league[4],
                    "code": league[5],
                    "flag": league[6]
                }
            })

        return {"response": leagues_list}

    except Exception as e:
        print(f"Error connecting to the database: {e}")
        return {"error": str(e)}
    finally:
        cursor.close()
        conn.close()
