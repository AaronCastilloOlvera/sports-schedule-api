import os
from fastapi import FastAPI
from dotenv import load_dotenv

load_dotenv()

DATABASE_URL = os.getenv("DATABASE_URL")
app = FastAPI()

@app.get("/")
def read_root():
    return {"message": "Hello, World!"}
