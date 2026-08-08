from sqlalchemy.orm import Session
from sqlalchemy import text
from fastapi import UploadFile
import models
import os

class BetService:

  def __init__(self, db: Session):
    self.db = db
    self.folder = "tickets_images"

  def get_tickets(self):
    return self.db.query(models.BettingTicket).order_by(models.BettingTicket.ticket_id.desc()).all()

  def get_leagues_by_sport(self, sport: str):
    rows = self.db.execute(text("""
        SELECT DISTINCT league FROM betting_tickets
        WHERE sport = :sport AND league IS NOT NULL AND league != ''
        ORDER BY league
    """), {'sport': sport}).fetchall()
    return [r.league for r in rows]

  def get_tickets_paginated(self, page: int = 0, limit: int = 10, search: str = ''):
    query = self.db.query(models.BettingTicket)
    if search:
      query = query.filter(models.BettingTicket.ticket_id.ilike(f'%{search}%'))
    total = query.count()
    data  = (query
             .order_by(models.BettingTicket.match_datetime.desc())
             .offset(page * limit)
             .limit(limit)
             .all())
    return {'total': total, 'page': page, 'limit': limit, 'data': data}

  def get_stats(self):
    row = self.db.execute(text("""
        SELECT
            COUNT(*)                                                             AS total,
            COUNT(*) FILTER (WHERE status = 'won')                              AS won,
            COUNT(*) FILTER (WHERE status IN ('won','lost'))                     AS won_or_lost,
            COALESCE(SUM(CASE WHEN status != 'pending' THEN net_profit ELSE 0 END), 0) AS net_profit,
            COALESCE(SUM(stake), 0)                                              AS total_staked,
            COALESCE(AVG(CASE WHEN odds > 0 THEN odds END), 0)                   AS avg_odds
        FROM betting_tickets
    """)).fetchone()
    won_or_lost = int(row.won_or_lost)
    return {
        'total':        int(row.total),
        'win_rate':     round(int(row.won) / won_or_lost * 100, 1) if won_or_lost else 0,
        'net_profit':   round(float(row.net_profit), 2),
        'total_staked': round(float(row.total_staked), 2),
        'avg_odds':     round(float(row.avg_odds), 2),
    }

  def get_analytics(self):
    from zoneinfo import ZoneInfo
    MX = ZoneInfo('America/Mexico_City')

    ODDS_BUCKETS = [
        ('1.00-1.50', 1.00, 1.50),
        ('1.50-2.00', 1.50, 2.00),
        ('2.00-3.00', 2.00, 3.00),
        ('3.00+',     3.00, float('inf')),
    ]
    TIME_BUCKETS = [
        ('Night (0-6)',        0,  6),
        ('Morning (6-12)',     6,  12),
        ('Afternoon (12-18)', 12, 18),
        ('Evening (18-24)',   18, 24),
    ]
    DAY_NAMES  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    DAY_ORDER  = ['Mon', 'Tue', 'Wed', 'Thu', 'Fri', 'Sat', 'Sun']
    EMPTY = {
        'accumulated_data': [], 'daily_data': [], 'win_loss_counts': {'won': 0, 'lost': 0, 'push': 0},
        'sport_data': [], 'league_data': [], 'bet_type_data': [], 'odds_bucket_data': [],
        'studied_data': [], 'day_of_week_data': [], 'time_of_day_data': [], 'daily_count_profit': [],
        'summary': {'best_day': None, 'best_bet_profit': None, 'streak': 0, 'streak_type': None, 'avg_odds': 0},
    }

    rows = self.db.execute(text("""
        SELECT sport, league, odds, stake, net_profit, status,
               match_datetime, bet_type, studied
        FROM betting_tickets
        WHERE status IN ('won','lost','push') AND match_datetime IS NOT NULL
        ORDER BY match_datetime
    """)).fetchall()

    if not rows:
        return EMPTY

    def fmt(dt):
        return dt.astimezone(MX).strftime('%d/%m')

    # accumulated_data
    acc = 0.0
    accumulated_data = [{'date': '', 'profit': 0}]
    for r in rows:
        acc += float(r.net_profit or 0)
        accumulated_data.append({'date': fmt(r.match_datetime), 'profit': round(acc, 2)})

    # daily_data — keep dict to deduplicate same day
    by_day: dict = {}
    for r in rows:
        d = fmt(r.match_datetime)
        by_day[d] = round((by_day.get(d) or 0.0) + float(r.net_profit or 0), 2)
    daily_data = [{'date': d, 'profit': p} for d, p in by_day.items()]

    # win_loss_counts
    win_loss_counts = {
        'won':  sum(1 for r in rows if r.status == 'won'),
        'lost': sum(1 for r in rows if r.status == 'lost'),
        'push': sum(1 for r in rows if r.status == 'push'),
    }

    # sport_data
    sm: dict = {}
    for r in rows:
        k = r.sport or 'Unknown'
        if k not in sm: sm[k] = {'p': 0.0, 'w': 0, 'n': 0, 's': 0.0}
        sm[k]['p'] += float(r.net_profit or 0); sm[k]['s'] += float(r.stake or 0); sm[k]['n'] += 1
        if r.status == 'won': sm[k]['w'] += 1
    sport_data = [{'sport': k, 'profit': round(v['p'], 2),
                   'winRate': round(v['w']/v['n']*100, 1) if v['n'] else 0,
                   'roi': round(v['p']/v['s']*100, 1) if v['s'] else 0,
                   'count': v['n']} for k, v in sm.items()]

    # league_data (top 10 by profit)
    lm: dict = {}
    for r in rows:
        k = r.league or 'Unknown'
        if k not in lm: lm[k] = {'p': 0.0, 'n': 0, 's': 0.0}
        lm[k]['p'] += float(r.net_profit or 0); lm[k]['s'] += float(r.stake or 0); lm[k]['n'] += 1
    league_data = sorted(
        [{'league': k, 'profit': round(v['p'], 2), 'roi': round(v['p']/v['s']*100, 1) if v['s'] else 0, 'count': v['n']}
         for k, v in lm.items()],
        key=lambda x: -x['profit'])

    # bet_type_data
    bm: dict = {}
    for r in rows:
        k = r.bet_type or 'Unknown'
        if k not in bm: bm[k] = {'p': 0.0, 'w': 0, 'n': 0, 's': 0.0}
        bm[k]['p'] += float(r.net_profit or 0); bm[k]['s'] += float(r.stake or 0); bm[k]['n'] += 1
        if r.status == 'won': bm[k]['w'] += 1
    bet_type_data = sorted(
        [{'betType': k, 'profit': round(v['p'], 2),
          'winRate': round(v['w']/v['n']*100, 1) if v['n'] else 0,
          'roi': round(v['p']/v['s']*100, 1) if v['s'] else 0,
          'count': v['n']} for k, v in bm.items()],
        key=lambda x: -x['profit'])

    # odds_bucket_data
    ob: dict = {b[0]: {'p': 0.0, 'w': 0, 'n': 0} for b in ODDS_BUCKETS}
    for r in rows:
        if not r.odds or r.odds <= 0: continue
        bucket = next((b for b in ODDS_BUCKETS if b[1] <= r.odds < b[2]), ODDS_BUCKETS[-1])
        ob[bucket[0]]['p'] += float(r.net_profit or 0); ob[bucket[0]]['n'] += 1
        if r.status == 'won': ob[bucket[0]]['w'] += 1
    odds_bucket_data = [{'range': lbl, 'profit': round(ob[lbl]['p'], 2),
                         'winRate': round(ob[lbl]['w']/ob[lbl]['n']*100, 1) if ob[lbl]['n'] else 0,
                         'count': ob[lbl]['n']}
                        for lbl, _, _ in ODDS_BUCKETS if ob[lbl]['n'] > 0]

    # studied_data
    st: dict = {'Studied': {'p': 0.0, 'w': 0, 'n': 0}, 'Not Studied': {'p': 0.0, 'w': 0, 'n': 0}}
    for r in rows:
        k = 'Studied' if r.studied else 'Not Studied'
        st[k]['p'] += float(r.net_profit or 0); st[k]['n'] += 1
        if r.status == 'won': st[k]['w'] += 1
    studied_data = [{'label': k, 'profit': round(v['p'], 2),
                     'winRate': round(v['w']/v['n']*100, 1) if v['n'] else 0,
                     'count': v['n']}
                    for k, v in st.items() if v['n'] > 0]

    # day_of_week_data
    dm: dict = {}
    for r in rows:
        day = DAY_NAMES[r.match_datetime.astimezone(MX).weekday()]
        if day not in dm: dm[day] = {'p': 0.0, 'w': 0, 'n': 0}
        dm[day]['p'] += float(r.net_profit or 0); dm[day]['n'] += 1
        if r.status == 'won': dm[day]['w'] += 1
    day_of_week_data = [{'day': d, 'profit': round(dm[d]['p'], 2),
                         'winRate': round(dm[d]['w']/dm[d]['n']*100, 1) if dm[d]['n'] else 0,
                         'count': dm[d]['n']}
                        for d in DAY_ORDER if d in dm]

    # time_of_day_data
    tm: dict = {b[0]: {'p': 0.0, 'w': 0, 'n': 0} for b in TIME_BUCKETS}
    for r in rows:
        hour = r.match_datetime.astimezone(MX).hour
        bucket = next((b for b in TIME_BUCKETS if b[1] <= hour < b[2]), None)
        if not bucket: continue
        tm[bucket[0]]['p'] += float(r.net_profit or 0); tm[bucket[0]]['n'] += 1
        if r.status == 'won': tm[bucket[0]]['w'] += 1
    time_of_day_data = [{'time': b[0], 'profit': round(tm[b[0]]['p'], 2),
                         'winRate': round(tm[b[0]]['w']/tm[b[0]]['n']*100, 1) if tm[b[0]]['n'] else 0,
                         'count': tm[b[0]]['n']}
                        for b in TIME_BUCKETS if tm[b[0]]['n'] > 0]

    # daily_count_profit (scatter)
    dc: dict = {}
    for r in rows:
        d = fmt(r.match_datetime)
        if d not in dc: dc[d] = {'p': 0.0, 'n': 0}
        dc[d]['p'] += float(r.net_profit or 0); dc[d]['n'] += 1
    daily_count_profit = [{'date': d, 'count': v['n'], 'profit': round(v['p'], 2)} for d, v in dc.items()]

    # summary stats
    best_day   = max(by_day.items(), key=lambda x: x[1]) if by_day else None
    best_bet   = max(rows, key=lambda r: float(r.net_profit or 0), default=None)
    wl_sorted  = sorted([r for r in rows if r.status in ('won','lost')],
                        key=lambda r: r.match_datetime, reverse=True)
    streak, streak_type = 0, None
    for r in wl_sorted:
        if not streak_type: streak_type = r.status; streak = 1
        elif r.status == streak_type: streak += 1
        else: break
    with_odds = [r for r in rows if r.odds and r.odds > 0]
    avg_odds  = round(sum(r.odds for r in with_odds) / len(with_odds), 2) if with_odds else 0

    return {
        'accumulated_data':   accumulated_data,
        'daily_data':         daily_data,
        'win_loss_counts':    win_loss_counts,
        'sport_data':         sport_data,
        'league_data':        league_data,
        'bet_type_data':      bet_type_data,
        'odds_bucket_data':   odds_bucket_data,
        'studied_data':       studied_data,
        'day_of_week_data':   day_of_week_data,
        'time_of_day_data':   time_of_day_data,
        'daily_count_profit': daily_count_profit,
        'summary': {
            'best_day':        {'date': best_day[0], 'profit': round(best_day[1], 2)} if best_day else None,
            'best_bet_profit': round(float(best_bet.net_profit or 0), 2) if best_bet else None,
            'streak':          streak,
            'streak_type':     streak_type,
            'avg_odds':        avg_odds,
        },
    }

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
