from sqlalchemy.orm import Session
from fastapi import UploadFile
from utils import models
import os

class BetService:

  def __init__(self, db: Session):
    self.db = db
    self.folder = "tickets_images"

  def get_tickets(self):
    return self.db.query(models.BettingTicket).all().order_by(models.BettingTicket.ticket_id.desc())
  
  def get_ticket_by_id(self, ticket_id: str):
    return self.db.query(models.BettingTicket).filter(models.BettingTicket.ticket_id == ticket_id).first()

  def create_ticket(self, ticket_data: dict):
    db_ticket = models.BettingTicket(**ticket_data)
    self.db.add(db_ticket)
    self.db.commit()
    self.db.refresh(db_ticket)
    return db_ticket
  
  def update_ticket(self, ticket_id: str, update_data: dict, file: UploadFile = None):
    db_ticket = self.db.query(models.BettingTicket).filter(models.BettingTicket.ticket_id == ticket_id).first()
    if not db_ticket:
      return False
    
    for key, value in update_data.items():
      if hasattr(db_ticket, key):
        setattr(db_ticket, key, value)

    image_path = self._save_image(ticket_id, file)
    if image_path:
      db_ticket.image_path = image_path

    self.db.commit()
    self.db.refresh(db_ticket)
    return db_ticket

  def delete_ticket(self, ticket_id: str):
     ticket = self.db.query(models.BettingTicket).filter(models.BettingTicket.ticket_id == ticket_id).first()
     if ticket:
        os.remove(ticket.image_path) if ticket.image_path else None
        self.db.delete(ticket)
        self.db.commit()
        return True
     return False
  
  def update_ticket_image(self, ticket_id: str, file: UploadFile):
    db_ticket = self.db.query(models.BettingTicket).filter(models.BettingTicket.ticket_id == ticket_id).first()
    if not db_ticket:
      return None
    
    image_path = self._save_image(ticket_id, file)
    db_ticket.image_path = image_path
    self.db.commit()
    self.db.refresh(db_ticket)
    return db_ticket

  def _save_image(self, ticket_id: str, file: UploadFile) -> str:
    if not (file and file.filename):
        return None
    
    os.makedirs(self.folder, exist_ok=True)
    
    extension = file.filename.split(".")[-1]
    file_name = f"{ticket_id}.{extension}"
    file_location = os.path.join(self.folder, file_name)

    with open(file_location, "wb") as buffer:
        buffer.write(file.file.read())

    return file_location
