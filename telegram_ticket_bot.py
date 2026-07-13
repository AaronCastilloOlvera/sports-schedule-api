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
from datetime import datetime
from dotenv import load_dotenv
from utils.database import SessionLocal
from models.betting_ticket import BettingTicket

load_dotenv()

BOT_TOKEN = os.getenv("TICKET_BOT_TOKEN")
ALLOWED_CHAT_ID = str(os.getenv("TICKET_CHAT_ID", ""))
OLLAMA_URL = os.getenv("OLLAMA_URL", "http://localhost:11434")
OLLAMA_MODEL = os.getenv("OLLAMA_MODEL", "gemma3")
TICKET_IMAGES_DIR = os.getenv("TICKET_IMAGES_DIR", "ticket_images")

TELEGRAM_API = f"https://api.telegram.org/bot{BOT_TOKEN}"

EXTRACT_PROMPT = """Analyze this betting ticket image and extract the information as a compact JSON object.
Return ONLY the JSON. No markdown, no code blocks, no explanation.

Rules:
- odds: always return as a positive decimal number >= 1.0. If the odds shown are American format (e.g. +150 → 2.50, -200 → 1.50, -320 → 1.31), convert them. Never return a negative number or a value less than 1.0 in this field.
- Remove special characters and extra spaces from text fields.
- league: NEVER return null if team names are visible. Use your sports knowledge to infer the league from the team names — football, baseball, basketball, tennis, etc. Only return null if the image is completely unreadable.
- pick: for parlays, list each selected team or outcome separated by ' + ' (e.g. "SF Giants + SD Padres"). Never return null if team names are visible.
- stake: use the total amount wagered shown on the ticket (e.g. "Apuesta total", "Total stake"). Ignore individual leg amounts.
- odds: use the combined/total odds shown on the ticket (e.g. "Momios" total), not individual leg odds.
- status: use 'pending' if no result is shown, 'won' if the ticket won, 'lost' if it lost.
- If the description contains 'incl. Prorroga', remove it.
- bet_type: prefix with 'CA - ' ONLY if it is a 'crear apuesta': one single match where the user combined 2 or more different markets (e.g. "Team A wins AND over 2.5 goals"). A single selection on one match is NOT a CA. Prefix with 'Parley' if it is a parlay (2 or more different matches combined).
- match_name: use 'Home Team vs Away Team' format. For parlays, list all matches separated by ' | '.
- device_type: 'movil' if the ticket looks like a mobile screenshot, 'desktop' if it looks like a desktop/web screenshot.
- pick: the selected outcome (e.g. 'Home', 'Over 2.5', 'Yes', 'Team A').

Expected JSON structure:
{
  "ticket_id": "ID printed on the ticket or null",
  "league": "competition name or null",
  "match_name": "Home Team vs Away Team",
  "bet_type": "bet market (e.g. 1X2, Over/Under 2.5, Parley, CA - 1X2 + BTTS)",
  "pick": "selected outcome",
  "odds": 1.85,
  "stake": 100.0,
  "payout": 185.0,
  "match_datetime": "YYYY-MM-DDTHH:MM:SS or null — if the year is not visible on the ticket, assume the current year",
  "status": "pending",
  "device_type": "movil",
  "studied": false,
  "comments": ""
}

Set any field to null if it cannot be read from the image."""


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
        "messages": [{"role": "user", "content": EXTRACT_PROMPT, "images": [image_b64]}],
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

def _to_decimal_odds(value) -> float | None:
    """Convert American or decimal odds to decimal format rounded to 2 places."""
    if value is None:
        return None
    try:
        v = float(str(value).replace(",", "."))
    except (ValueError, TypeError):
        return None
    # American format: >= 100 or <= -100
    if v >= 100:
        return round(1 + v / 100, 2)
    if v <= -100:
        return round(1 + 100 / abs(v), 2)
    # Negative decimal odds don't exist — model likely misread American odds (e.g. -3.2 → -320)
    if v < 1:
        return None
    # Already decimal
    return round(v, 2)


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

    match_dt = None
    raw_dt = data.get("match_datetime")
    if raw_dt:
        try:
            match_dt = datetime.fromisoformat(raw_dt)
        except (ValueError, TypeError):
            pass

    stake = data.get("stake")
    payout = data.get("payout")
    net_profit = round(payout - stake, 2) if (payout is not None and stake is not None) else None

    ticket = BettingTicket(
        ticket_id=ticket_id,
        league=_clean(data.get("league")),
        match_name=_clean(data.get("match_name")),
        bet_type=_clean(data.get("bet_type")),
        pick=_clean(data.get("pick")),
        odds=_to_decimal_odds(data.get("odds")),
        stake=stake,
        payout=payout,
        net_profit=net_profit,
        match_datetime=match_dt or datetime.utcnow(),
        status=data.get("status") or "pending",
        sport="Futbol",
        device_type=data.get("device_type"),
        studied=data.get("studied") or False,
        comments=data.get("comments") or "",
        image_path=image_path,
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

def handle_photo(message: dict) -> None:
    chat_id = str(message["chat"]["id"])

    send_message(chat_id, "Analizando ticket... (puede tardar hasta 1 minuto)")

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
        if text.startswith("/won") or text.startswith("/lost"):
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
