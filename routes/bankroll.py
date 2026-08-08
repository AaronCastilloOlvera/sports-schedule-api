from fastapi import APIRouter, Depends, Query
from sqlalchemy.orm import Session
from utils import database, schemas
from services.bankroll_service import BankrollService

router = APIRouter(prefix="/bankroll", tags=["Bankroll"])

@router.get("/summary")
def get_summary(db: Session = Depends(database.get_db)):
    return BankrollService(db).get_summary()

@router.get("/chart-data")
def get_chart_data(db: Session = Depends(database.get_db)):
    return BankrollService(db).get_chart_data()

@router.get("/transactions")
def get_transactions(
    page:  int = Query(0,  ge=0),
    limit: int = Query(10, ge=1, le=100),
    db: Session = Depends(database.get_db),
):
    return BankrollService(db).get_transactions(page=page, limit=limit)

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
