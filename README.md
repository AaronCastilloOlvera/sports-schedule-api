# sports-schedule-api

## Python Commands

### 1. Create and activate a virtual environment

```bash
python3 -m venv venv
venv\Scripts\activate.bat  # On Windows (CMD)
```

### 2. Install main dependencies 

```bash
pip install fastapi
pip install uvicorn
pip install python-dotenv
pip install orjson
```

### Alternatively, install all dependencies fron a file

```bash
pip install -r requirements.txt
```

### 3. Activate Local Server
```bash
venv\Scripts\activate.bat
```

### 4. Run the Local Server
```bash
uvicorn main:app --reload
```

---

## AI Assistance

At the start of every new Claude Code session, paste the full contents of `context.md` as your first message. After that, just chat normally — no special format needed.

---

# How to create an Migration

```bash
alembic revision --autogenerate -m "message of migration"
```

```bash
alembic upgrade head
```
