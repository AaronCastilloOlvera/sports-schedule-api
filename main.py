from fastapi import FastAPI
from fastapi.responses import JSONResponse
from pydantic import BaseModel

app = FastAPI()

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

