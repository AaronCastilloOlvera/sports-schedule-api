import os
from sqlalchemy.orm import Session
from utils import models
from fastapi import UploadFile

def create_betting_ticket(db: Session, ticket_data: dict, file: UploadFile = None):    

  db_ticket = models.BettingTicket(**ticket_data)
  db.add(db_ticket)
  db.commit()
  db.refresh(db_ticket)

  if file and isinstance(file, UploadFile) and file.filename:
      folder = "tickets_images"
      if not os.path.exists(folder):
          os.makedirs(folder)
      
      extension = file.filename.split(".")[-1]
      file_name = f"{db_ticket.ticket_id}.{extension}"
      file_location = os.path.join(folder, file_name)

      with open(file_location, "wb") as buffer:
          buffer.write(file.file.read())

      db_ticket.image_path = file_location
      db.commit()
      db.refresh(db_ticket)

  return db_ticket

def get_all_betting_tickets(db: Session, skip: int = 0, limit: int = 100):
  return db.query(models.BettingTicket).offset(skip).limit(limit).all()