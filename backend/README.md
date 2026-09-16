# STTM Backend (standalone)

FastAPI service for profiling, mapping, HL7 → FHIR, and extract workflows.

## Run locally

```bash
python3 -m venv .venv
source .venv/bin/activate   # Windows: .venv\Scripts\activate
pip install -r requirements.txt
cp .env.example .env        # add GOOGLE_API_KEY
uvicorn api.main:app --reload --host 0.0.0.0 --port 8000
```

Health: http://127.0.0.1:8000/health

## Docker

From parent folder: `docker build -t sttm-backend:latest ./backend`

Or use `../docker-compose.yml`.

## Entry point

`api/main.py` — not `web/main.py` (Launchpad adapter only; included for reference).
