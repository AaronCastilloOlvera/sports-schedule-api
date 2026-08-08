from sqlalchemy.orm import Session
from sqlalchemy import text
from models.bankroll_transaction import BankrollTransaction

class BankrollService:
    def __init__(self, db: Session):
        self.db = db

    def get_summary(self):
        tx = self.db.execute(text("""
            SELECT
                COALESCE(SUM(CASE WHEN type = 'deposit'    THEN amount ELSE 0 END), 0) AS total_deposits,
                COALESCE(SUM(CASE WHEN type = 'withdrawal' THEN amount ELSE 0 END), 0) AS total_withdrawals,
                COALESCE(SUM(CASE WHEN type = 'nu_expense' THEN amount ELSE 0 END), 0) AS nu_expenses
            FROM bankroll_transactions
        """)).fetchone()

        tk = self.db.execute(text("""
            SELECT
                COALESCE(SUM(net_profit), 0)                              AS bets_net_profit,
                COALESCE(SUM(stake), 0)                                   AS total_staked,
                COUNT(*) FILTER (WHERE status = 'won')                    AS win_count,
                COUNT(*) FILTER (WHERE status = 'lost')                   AS loss_count,
                COUNT(*) FILTER (WHERE status = 'push')                   AS push_count
            FROM betting_tickets
            WHERE status IN ('won', 'lost', 'push')
        """)).fetchone()

        total_deposits    = float(tx.total_deposits)
        total_withdrawals = float(tx.total_withdrawals)
        nu_expenses       = float(tx.nu_expenses)
        bets_net_profit   = float(tk.bets_net_profit)
        total_staked      = float(tk.total_staked)

        nu_balance   = total_withdrawals * 0.99 - nu_expenses
        real_balance = total_deposits - total_withdrawals + bets_net_profit
        roi          = (bets_net_profit / total_staked * 100) if total_staked > 0 else 0

        return {
            "total_deposits":    total_deposits,
            "total_withdrawals": total_withdrawals,
            "nu_expenses":       nu_expenses,
            "bets_net_profit":   bets_net_profit,
            "total_staked":      total_staked,
            "nu_balance":        nu_balance,
            "real_balance":      real_balance,
            "roi":               round(roi, 2),
            "win_count":         tk.win_count,
            "loss_count":        tk.loss_count,
            "push_count":        tk.push_count,
        }

    def get_transactions(self, page: int = 0, limit: int = 10):
        offset = page * limit
        total = self.db.query(BankrollTransaction).count()
        rows  = (
            self.db.query(BankrollTransaction)
            .order_by(BankrollTransaction.date.desc())
            .offset(offset)
            .limit(limit)
            .all()
        )
        return {"total": total, "page": page, "limit": limit, "data": rows}

    def get_chart_data(self):
        from datetime import datetime, timedelta

        MONTH_ES = {1: 'ene', 2: 'feb', 3: 'mar', 4: 'abr', 5: 'may', 6: 'jun',
                    7: 'jul', 8: 'ago', 9: 'sep', 10: 'oct', 11: 'nov', 12: 'dic'}

        def get_week_start(date_str):
            d = datetime.strptime(str(date_str)[:10], '%Y-%m-%d')
            js_day = (d.weekday() + 1) % 7  # JS: Sun=0, Mon=1 … Python: Mon=0, Sun=6
            offset = -6 if js_day == 0 else 1 - js_day
            return (d + timedelta(days=offset)).strftime('%Y-%m-%d')

        def fmt_week_label(date_str):
            return datetime.strptime(str(date_str)[:10], '%Y-%m-%d').strftime('%d/%m')

        def fmt_month_label(month_str):
            year, month = month_str.split('-')
            return f"{MONTH_ES[int(month)]} {year}"

        txs = self.db.execute(text("""
            SELECT date, type, amount FROM bankroll_transactions ORDER BY date
        """)).fetchall()

        tks = self.db.execute(text("""
            SELECT match_datetime, net_profit, stake
            FROM betting_tickets
            WHERE status IN ('won', 'lost', 'push') AND match_datetime IS NOT NULL
            ORDER BY match_datetime
        """)).fetchall()

        # weeklyWithdrawals
        by_week: dict = {}
        for tx in txs:
            if tx.type == 'withdrawal':
                week = get_week_start(str(tx.date)[:10])
                by_week[week] = round((by_week.get(week) or 0.0) + float(tx.amount), 2)
        weekly_withdrawals = [
            {'week': fmt_week_label(w), 'total': total}
            for w, total in sorted(by_week.items())
        ]

        # monthlyFlow
        by_month: dict = {}
        for tx in txs:
            m = str(tx.date)[:7]
            if m not in by_month:
                by_month[m] = {'deposited': 0.0, 'withdrawn': 0.0}
            if tx.type == 'deposit':
                by_month[m]['deposited'] += float(tx.amount)
            elif tx.type == 'withdrawal':
                by_month[m]['withdrawn'] += float(tx.amount)
        monthly_flow = []
        for m in sorted(by_month):
            dep = round(by_month[m]['deposited'], 2)
            wd  = round(by_month[m]['withdrawn'], 2)
            has = dep > 0
            monthly_flow.append({
                'month':       fmt_month_label(m),
                'deposited':   dep,
                'withdrawn':   wd if has else None,
                'net':         round(wd - dep, 2),
                'hasDeposits': has,
            })

        # balanceHistory — all tx deltas + ticket net_profit, accumulated by date
        events: list = []
        for tx in txs:
            if tx.type != 'nu_expense':
                delta = float(tx.amount) if tx.type == 'deposit' else -float(tx.amount)
                events.append((str(tx.date)[:10], delta))
        for tk in tks:
            events.append((str(tk.match_datetime)[:10], float(tk.net_profit or 0)))
        events.sort(key=lambda e: e[0])

        by_date: dict = {}
        running = 0.0
        for date_str, delta in events:
            running += delta
            by_date[date_str] = running
        balance_history = [
            {'date': fmt_week_label(d), 'balance': round(v, 2)}
            for d, v in sorted(by_date.items())
        ]

        # cumulativeWithdrawals
        wd_by_date: dict = {}
        for tx in txs:
            if tx.type == 'withdrawal':
                d = str(tx.date)[:10]
                wd_by_date[d] = (wd_by_date.get(d) or 0.0) + float(tx.amount)
        running = 0.0
        cumulative_withdrawals = []
        for d, amt in sorted(wd_by_date.items()):
            running += amt
            cumulative_withdrawals.append({'date': fmt_week_label(d), 'total': round(running, 2)})

        # stakeVsBankroll — for each resolved ticket, bankroll before the bet
        all_events: list = []
        for tx in txs:
            if tx.type != 'nu_expense':
                delta = float(tx.amount) if tx.type == 'deposit' else -float(tx.amount)
                all_events.append((str(tx.date)[:10] + 'T00:00:00', delta))
        for tk in tks:
            all_events.append((str(tk.match_datetime)[:19], float(tk.net_profit or 0)))
        all_events.sort(key=lambda e: e[0])

        stake_vs_bankroll = []
        n = 0
        for tk in sorted(tks, key=lambda t: str(t.match_datetime)):
            stake = float(tk.stake or 0)
            if stake <= 0:
                continue
            bet_time = str(tk.match_datetime)[:19]
            bankroll_before = sum(delta for time_key, delta in all_events if time_key < bet_time)
            if bankroll_before <= 0:
                continue
            pct = round((stake / bankroll_before) * 100, 1)
            n += 1
            stake_vs_bankroll.append({
                'n':        n,
                'date':     fmt_week_label(str(tk.match_datetime)[:10]),
                'stake':    round(stake, 2),
                'bankroll': round(bankroll_before, 2),
                'pct':      pct,
            })

        return {
            'weekly_withdrawals':     weekly_withdrawals,
            'monthly_flow':           monthly_flow,
            'balance_history':        balance_history,
            'cumulative_withdrawals': cumulative_withdrawals,
            'stake_vs_bankroll':      stake_vs_bankroll,
        }

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
