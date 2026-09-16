"""
telegram_ticket_bot.py
======================
Standalone Telegram bot for saving betting tickets to the database.

This script is independent from the main FastAPI backend and the frontend —
it shares the same PostgreSQL database (via SQLAlchemy) but runs as a
separate process, designed to be started locally alongside the main app.

How it works
------------
1. Listens for incoming Telegram messages via long-polling (no webhook needed).
2. When a photo is received, it downloads the image and sends it to a local
   Ollama vision model (default: qwen2.5vl:7b) which extracts structured
   ticket data as JSON.
3. The extracted data is validated, odds are converted to decimal format,
   and the ticket is saved to the `betting_tickets` table in PostgreSQL.
4. A summary of the saved ticket is sent back to the user via Telegram.

Commands
--------
  /won <ticket_id>   Mark a ticket as won.
  /lost <ticket_id>  Mark a ticket as lost.

Tech stack
----------
  - Telegram Bot API  : long-polling via requests (no extra library)
  - Vision inference  : Ollama REST API (/api/chat) running locally
  - Database          : PostgreSQL on Railway, accessed via SQLAlchemy
  - Image storage     : local filesystem (ticket_images/)

Future
------
  Currently designed to run locally with Ollama as the vision provider.
  The vision step may be migrated to a cloud OCR/vision service (e.g.
  Google Document AI, AWS Textract) to allow the bot to run on a server
  without a local GPU.

Environment variables
---------------------
  TICKET_BOT_TOKEN   Telegram bot token (separate from the pipeline bot).
  TICKET_CHAT_ID     Your personal Telegram chat ID (restricts access to you only).
  OLLAMA_URL         Ollama base URL (default: http://localhost:11434).
  OLLAMA_MODEL       Vision model name (default: qwen2.5vl:7b).
  TICKET_IMAGES_DIR  Local folder for saving ticket images (default: ticket_images/).
  DATABASE_URL       PostgreSQL connection string (shared with the main app).
"""

import os
import json
import uuid
import base64
import time
import requests
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
from dotenv import load_dotenv
from sqlalchemy import text
from utils.database import SessionLocal
from utils.odds import normalize_odds
from models.betting_ticket import BettingTicket

load_dotenv()

BOT_TOKEN = os.getenv("TICKET_BOT_TOKEN")
ALLOWED_CHAT_ID = str(os.getenv("TICKET_CHAT_ID", ""))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3")
TICKET_IMAGES_DIR = os.getenv("TICKET_IMAGES_DIR", "ticket_images")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

# When True, photos are analyzed but NOT saved to DB — JSON is returned instead.
preview_mode: bool = False


def _build_prompt() -> str:
    year = datetime.now().year
    return f"""Extract data from this betting ticket image and return ONLY a JSON object. No markdown, no explanation.

If the year is not visible, use {year}.

STEP 1 — Determine bet_type:
- If the ticket shows "SGP" → bet_type = "crear_apuesta" (always, no exceptions)
- If 2+ different matches combined → bet_type = "parlay"
- If 1 match AND 2+ selections on the same game → bet_type = "crear_apuesta"
- If 1 match AND 1 selection → bet_type = "simple"

STEP 2 — Fill legs (REQUIRED for crear_apuesta and parlay, null for simple):
List every individual selection. Each leg:
  match_name: "Team A vs Team B"
  league: competition name
  pick: human-readable description (e.g. "Yellow Cards Under 5.5")
  market: one of goals / corners / cards / btts / moneyline / other
  side: over / under / yes / no / home / away / null
  line_used: numeric line (e.g. 5.5) or null
  odd: individual leg odd if shown, else null
  outcome: true if won, false if lost, null if pending
  pick: ALWAYS in English using this exact format: "Market Side Line" — e.g. "Corners Under 11.5", "Yellow Cards Under 5.5", "Goals Over 2.5", "BTTS Yes". Never use the ticket's original language.

STEP 3 — Fill remaining fields:
- ticket_id: ID printed on the ticket, or null
- sport: futbol / basketball / american_football / baseball (infer from teams, never null)
- league: competition name (NFL/MLS/NBA/MLB/LMB in ALL CAPS; others PascalCase). For multi-league parlay use "Parlay" in this top-level field ONLY. Never the sport name itself.
- legs[].league: ALWAYS the specific competition of that individual match — NEVER "Parlay". Infer it from the team names.
- match_name: "Away Team vs Home Team". For parlay: "Match1 | Match2". For US sports prefix city abbreviation (e.g. "HOU Astros vs LA Angels").
- pick: all selections joined with " + " (e.g. "Yellow Cards Under 5.5 + Corners Under 11.5")
- odds: TOTAL combined odds exactly as shown (e.g. -132, +210, 2.10). Do NOT convert. Use 0 if unreadable.
- stake: total amount wagered ("Apuesta total"). Never a per-leg amount.
- payout: total payout shown, or null
- match_datetime: YYYY-MM-DDTHH:MM:SS, use {year} if year not visible
- status: won / lost / push / pending (pending if no result shown)
- device_type: mobile / desktop

Return this exact structure:
{{
  "ticket_id": "5351285259",
  "sport": "futbol",
  "league": "MLS",
  "match_name": "Inter Miami CF vs CF Montreal",
  "bet_type": "crear_apuesta",
  "pick": "Yellow Cards Under 5.5 + Corners Under 11.5",
  "odds": -132,
  "stake": 1000.0,
  "payout": null,
  "match_datetime": "2026-08-29T17:30:00",
  "status": "lost",
  "device_type": "mobile",
  "studied": false,
  "comments": "",
  "legs": [
    {{"match_name": "Inter Miami CF vs CF Montreal", "league": "MLS", "pick": "Cards Under 5.5", "market": "cards", "side": "under", "line_used": 5.5, "odd": null, "outcome": true}},
    {{"match_name": "Inter Miami CF vs CF Montreal", "league": "MLS", "pick": "Corners Under 11.5", "market": "corners", "side": "under", "line_used": 11.5, "odd": null, "outcome": false}}
  ]
}}"""


# ---------------------------------------------------------------------------
# Telegram helpers
# ---------------------------------------------------------------------------

def send_message(chat_id: str, text: str) -> None:
    try:
        requests.post(
            f"{TELEGRAM_API}/sendMessage",
            json={"chat_id": chat_id, "text": text, "parse_mode": "HTML"},
            timeout=10,
        )
    except Exception as e:
        print(f"[telegram] send_message error: {e}")


def download_photo(file_id: str) -> bytes:
    r = requests.get(f"{TELEGRAM_API}/getFile", params={"file_id": file_id}, timeout=10)
    r.raise_for_status()
    file_path = r.json()["result"]["file_path"]
    r2 = requests.get(f"https://api.telegram.org/file/bot{BOT_TOKEN}/{file_path}", timeout=30)
    r2.raise_for_status()
    return r2.content


# ---------------------------------------------------------------------------
# Ollama vision
# ---------------------------------------------------------------------------

def extract_ticket_data(image_bytes: bytes) -> dict:
    image_b64 = base64.b64encode(image_bytes).decode("utf-8")

    payload = {
        "model": OLLAMA_MODEL,
        "messages": [{"role": "user", "content": _build_prompt(), "images": [image_b64]}],
        "stream": False,
    }

    r = requests.post(f"{OLLAMA_URL}/api/chat", json=payload, timeout=120)
    r.raise_for_status()

    content = r.json()["message"]["content"].strip()

    if content.startswith("```"):
        lines = content.split("\n")
        lines = [l for l in lines if not l.startswith("```")]
        content = "\n".join(lines).strip()

    return json.loads(content)


# ---------------------------------------------------------------------------
# DB persistence
# ---------------------------------------------------------------------------


def _clean(value):
    """Return None if value is missing, the string 'null', or 'none'."""
    if value is None:
        return None
    if str(value).strip().lower() in ("null", "none", ""):
        return None
    return value



def save_ticket(data: dict, image_bytes: bytes) -> BettingTicket:
    ticket_id = _clean(data.get("ticket_id")) or str(uuid.uuid4())

    os.makedirs(TICKET_IMAGES_DIR, exist_ok=True)
    image_path = os.path.join(TICKET_IMAGES_DIR, f"{ticket_id}.jpg")
    with open(image_path, "wb") as f:
        f.write(image_bytes)

    MX_TZ = ZoneInfo("America/Mexico_City")
    match_dt = None
    raw_dt = data.get("match_datetime")
    if raw_dt:
        try:
            naive = datetime.fromisoformat(raw_dt)
            # The model extracts local MX time from the ticket image.
            # Attach the MX timezone so PostgreSQL stores the correct UTC equivalent.
            match_dt = naive.replace(tzinfo=MX_TZ)
        except (ValueError, TypeError):
            pass

    stake = data.get("stake")
    status = data.get("status") or "pending"

    if status == "lost" and stake is not None:
        payout = 0.0
        net_profit = round(-stake, 2)
    else:
        payout = data.get("payout")
        net_profit = round(payout - stake, 2) if (payout is not None and stake is not None) else None

    try:
        odds = normalize_odds(float(data.get("odds") or 0) or None)
    except (ValueError, TypeError):
        odds = None

    ticket = BettingTicket(
        ticket_id=ticket_id,
        league=_clean(data.get("league")),
        match_name=_clean(data.get("match_name")),
        bet_type=_clean(data.get("bet_type")),
        pick=_clean(data.get("pick")),
        odds=odds,
        stake=stake,
        payout=payout,
        net_profit=net_profit,
        match_datetime=match_dt or datetime.now(timezone.utc),
        status=status,
        sport=_clean(data.get("sport")) or "futbol",
        device_type=data.get("device_type"),
        studied=data.get("studied") or False,
        comments=data.get("comments") or "",
        image_path=image_path,
        legs=data.get("legs"),
    )

    db = SessionLocal()
    try:
        existing = db.query(BettingTicket).filter(BettingTicket.ticket_id == ticket_id).first()
        if existing:
            raise ValueError(f"El ticket {ticket_id} ya existe en la base de datos.")
        db.add(ticket)
        db.commit()
        db.refresh(ticket)
        return ticket
    finally:
        db.close()


# ---------------------------------------------------------------------------
# Message handlers
# ---------------------------------------------------------------------------

_MARKET_LABEL = {
    "goals": "Goals", "total": "Goals",
    "corners": "Corners",
    "cards": "Cards", "yellow_cards": "Cards",
    "btts": "BTTS", "moneyline": "Moneyline", "other": "Other",
}
_SIDE_LABEL = {
    "over": "Over", "under": "Under",
    "yes": "Yes", "no": "No",
    "home": "Home", "away": "Away",
}

def _normalize_leg_pick(leg: dict) -> str:
    market = _MARKET_LABEL.get(leg.get("market", ""), leg.get("market", ""))
    side = _SIDE_LABEL.get(leg.get("side", ""), "")
    line = leg.get("line_used")
    parts = [p for p in [market, side, str(line) if line is not None else None] if p]
    return " ".join(parts)


def _lookup_league(match_name: str, match_datetime: str | None) -> str | None:
    """Query fixtures DB to find the league for a match. Returns None if not found."""
    if not match_name:
        return None
    # For parlays ("Match1 | Match2") only use the first match
    first_match = match_name.split("|")[0].strip()
    parts = [p.strip() for p in first_match.replace(" vs. ", " vs ").split(" vs ")]
    if len(parts) < 2:
        return None
    home, away = parts[0], parts[1]

    date_filter = ""
    params: dict = {"home": f"%{home}%", "away": f"%{away}%"}
    if match_datetime:
        try:
            params["date"] = match_datetime[:10]
            date_filter = "AND DATE(f.date_utc) = :date"
        except Exception:
            pass

    db = SessionLocal()
    try:
        row = db.execute(text(f"""
            SELECT l.name
            FROM fixtures f
            JOIN leagues l ON l.id = f.league_id
            WHERE (f.home_team_name ILIKE :home OR f.away_team_name ILIKE :home)
              AND (f.home_team_name ILIKE :away OR f.away_team_name ILIKE :away)
              {date_filter}
            LIMIT 1
        """), params).fetchone()
        return row[0] if row else None
    except Exception as e:
        print(f"[db] league lookup error: {e}")
        return None
    finally:
        db.close()


def handle_photo(message: dict) -> None:
    global preview_mode
    chat_id = str(message["chat"]["id"])

    mode_tag = " [PREVIEW — no se guardará]" if preview_mode else ""
    send_message(chat_id, f"Analizando ticket...{mode_tag} (puede tardar hasta 1 minuto)")

    photo = message["photo"][-1]  # highest resolution
    try:
        image_bytes = download_photo(photo["file_id"])
    except Exception as e:
        send_message(chat_id, f"No pude descargar la imagen: {e}")
        return

    try:
        data = extract_ticket_data(image_bytes)
    except json.JSONDecodeError as e:
        send_message(chat_id, f"El modelo no devolvio JSON valido: {e}")
        return
    except Exception as e:
        send_message(chat_id, f"Error al analizar la imagen con Ollama: {e}")
        return

    # Override league using DB lookup
    if data.get("bet_type") != "parlay":
        db_league = _lookup_league(data.get("match_name"), data.get("match_datetime"))
        if db_league:
            data["league"] = db_league

    # For every leg (crear_apuesta and parlay), look up league and normalize pick
    normalized_picks = []
    for leg in data.get("legs") or []:
        db_league = _lookup_league(leg.get("match_name"), data.get("match_datetime"))
        if db_league:
            leg["league"] = db_league
        leg["pick"] = _normalize_leg_pick(leg)
        normalized_picks.append(leg["pick"])

    # Rebuild top-level pick from normalized legs
    if normalized_picks:
        data["pick"] = " + ".join(normalized_picks)

    if preview_mode:
        pretty = json.dumps(data, ensure_ascii=False, indent=2)
        send_message(chat_id, f"Preview (no guardado):\n\n{pretty}")
        return

    try:
        ticket = save_ticket(data, image_bytes)
    except Exception as e:
        send_message(chat_id, f"Error guardando en la base de datos: {e}")
        return

    match_dt_str = ticket.match_datetime.strftime("%d/%m/%Y %H:%M") if ticket.match_datetime else "N/A"
    net = f"{ticket.net_profit:+.2f}" if ticket.net_profit is not None else "N/A"
    summary = (
        f"<b>Ticket guardado</b>\n\n"
        f"Partido: {ticket.match_name or 'N/A'}\n"
        f"Fecha: {match_dt_str}\n"
        f"Liga: {ticket.league or 'N/A'}\n"
        f"Tipo: {ticket.bet_type or 'N/A'}\n"
        f"Pick: {ticket.pick or 'N/A'}\n"
        f"Cuota: {ticket.odds or 'N/A'}\n"
        f"Stake: {ticket.stake or 'N/A'}\n"
        f"{'Ganancia' if ticket.status == 'won' else 'Pago potencial'}: {ticket.payout or 'N/A'}\n"
        f"Utilidad neta: {net}\n"
        f"Estado: {ticket.status}\n"
        f"Dispositivo: {ticket.device_type or 'N/A'}\n"
        f"ID: <code>{ticket.ticket_id}</code>"
    )
    send_message(chat_id, summary)
    send_message(chat_id, "Listo. Puedes mandar otro ticket.")


def handle_update(update: dict) -> None:
    message = update.get("message", {})
    chat_id = str(message.get("chat", {}).get("id", ""))

    # Ignore messages from unauthorized chats
    if ALLOWED_CHAT_ID and chat_id != ALLOWED_CHAT_ID:
        return

    if "photo" in message:
        handle_photo(message)
    elif "text" in message:
        text = message["text"].strip()
        if text.startswith("/preview"):
            global preview_mode
            preview_mode = not preview_mode
            state = "activado ✅" if preview_mode else "desactivado ❌"
            send_message(chat_id, f"Modo preview {state}. Las fotos {'NO se guardarán, solo retornaré el JSON extraído.' if preview_mode else 'se guardarán normalmente en la base de datos.'}")
        elif text.startswith("/won") or text.startswith("/lost"):
            parts = text.split()
            if len(parts) < 2:
                send_message(chat_id, "Uso: /won &lt;ticket_id&gt; o /lost &lt;ticket_id&gt;")
            else:
                new_status = "won" if text.startswith("/won") else "lost"
                ticket_id = parts[1]
                db = SessionLocal()
                try:
                    ticket = db.query(BettingTicket).filter(BettingTicket.ticket_id == ticket_id).first()
                    if not ticket:
                        send_message(chat_id, f"No encontré el ticket <code>{ticket_id}</code>.")
                    else:
                        ticket.status = new_status
                        db.commit()
                        emoji = "✅" if new_status == "won" else "❌"
                        send_message(chat_id, f"{emoji} Ticket <code>{ticket_id}</code> marcado como <b>{new_status}</b>.")
                finally:
                    db.close()


# ---------------------------------------------------------------------------
# Polling loop
# ---------------------------------------------------------------------------

def run() -> None:
    if not BOT_TOKEN:
        raise RuntimeError("TELEGRAM_BOT_TOKEN no esta configurado")

    print(f"Bot iniciado. Modelo: {OLLAMA_MODEL} en {OLLAMA_URL}")

    if ALLOWED_CHAT_ID:
        send_message(ALLOWED_CHAT_ID, "✅ Bot iniciado. Ya estoy escuchando — mándame una foto de tu ticket.")

    offset = None

    while True:
        try:
            params = {"timeout": 30, "allowed_updates": ["message"]}
            if offset is not None:
                params["offset"] = offset

            r = requests.get(f"{TELEGRAM_API}/getUpdates", params=params, timeout=35)
            r.raise_for_status()
            updates = r.json().get("result", [])

            for update in updates:
                offset = update["update_id"] + 1
                try:
                    handle_update(update)
                except Exception as e:
                    print(f"[bot] error procesando update {update['update_id']}: {e}")

        except requests.exceptions.Timeout:
            pass  # normal — long-polling timeout, just loop again
        except Exception as e:
            print(f"[polling] error: {e}")
            time.sleep(5)


if __name__ == "__main__":
    run()
