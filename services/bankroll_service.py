from sqlalchemy.orm import Session
from models.bankroll_transaction import BankrollTransaction

class BankrollService:
    def __init__(self, db: Session):
        self.db = db

    def get_transactions(self):
        return self.db.query(BankrollTransaction).order_by(BankrollTransaction.date.desc()).all()

    def create_transaction(self, data: dict):
        tx = BankrollTransaction(**data)
        self.db.add(tx)
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def update_transaction(self, tx_id: int, data: dict):
        tx = self.db.query(BankrollTransaction).filter(BankrollTransaction.id == tx_id).first()
        if not tx:
            return None
        for key, value in data.items():
            if hasattr(tx, key):
                setattr(tx, key, value)
        self.db.commit()
        self.db.refresh(tx)
        return tx

    def delete_transaction(self, tx_id: int):
        tx = self.db.query(BankrollTransaction).filter(BankrollTransaction.id == tx_id).first()
        if not tx:
            return False
        self.db.delete(tx)
        self.db.commit()
        return True
