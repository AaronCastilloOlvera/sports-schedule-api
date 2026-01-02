from sqlalchemy.orm import Session
from fastapi import UploadFile
import os
from utils import models

class BetService:

  def __init__(self, db: Session):
    self.db = db

  def get_tickets(self):
    return self.db.query(models.BettingTicket).all()

  def create_ticket(self, ticket_data: dict, file: UploadFile = None):
    db_ticket = models.BettingTicket(**ticket_data)
    self.db.add(db_ticket)
    self.db.commit()
    self.db.refresh(db_ticket)
    if file and isinstance(file, UploadFile) and file.filename:
        folder = "tickets_images"
        if not os.path.exists():
            os.makedirs(folder)
        
        extension = file.filename.split(".")[-1]
        file_name = f"{db_ticket.ticket_id}.{extension}"
        file_location = os.path.join(folder, file_name)

        with open(file_location, "wb") as buffer:
            buffer.write(file.file.read())

        db_ticket.image_path = file_location
        self.db.commit()
        self.db.refresh(db_ticket)
        
    return db_ticket
  
  def delete_ticket(self, ticket_id: str):
     ticket = self.db.query(models.BettingTicket).filter(models.BettingTicket.ticket_id == ticket_id).first()
     if ticket:
        self.db.delete(ticket)
        self.db.commit()
       

    
