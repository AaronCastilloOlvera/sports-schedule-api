from sqlalchemy.orm import Session
from utils import models, schemas

def create_betting_ticket(db: Session, ticket: schemas.BettingTicketCreate):
    db_ticket = models.BettingTicket(**ticket.dict())
    db.add(db_ticket)
    db.commit()
    db.refresh(db_ticket)
    return db_ticket

def get_all_betting_tickets(db: Session, skip: int = 0, limit: int = 100):
    return db.query(models.BettingTicket).offset(skip).limit(limit).all()