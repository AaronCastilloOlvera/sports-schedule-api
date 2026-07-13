from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from utils import database, schemas
from services.bankroll_service import BankrollService

router = APIRouter(prefix="/bankroll", tags=["Bankroll"])

@router.get("/transactions", response_model=list[schemas.BankrollTransaction])
def get_transactions(db: Session = Depends(database.get_db)):
    return BankrollService(db).get_transactions()

@router.post("/transactions", response_model=schemas.BankrollTransaction)
def create_transaction(tx: schemas.BankrollTransactionCreate, db: Session = Depends(database.get_db)):
    return BankrollService(db).create_transaction(tx.dict())

@router.put("/transactions/{tx_id}", response_model=schemas.BankrollTransaction)
def update_transaction(tx_id: int, tx: schemas.BankrollTransactionCreate, db: Session = Depends(database.get_db)):
    return BankrollService(db).update_transaction(tx_id, tx.dict())

@router.delete("/transactions/{tx_id}")
def delete_transaction(tx_id: int, db: Session = Depends(database.get_db)):
    success = BankrollService(db).delete_transaction(tx_id)
    if not success:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Transaction not found")
    return {"message": "Deleted"}
