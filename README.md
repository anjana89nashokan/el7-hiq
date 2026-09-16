# STTM Standalone

Self-contained **Source-to-Target Mapping (STTM)** application. Copy this entire folder to run or deploy outside the AI Launchpad monorepo.

```
sttm-standalone/
├── backend/          # FastAPI API (agents, profiling, mapping, HL7)
├── frontend/         # React + Vite UI
├── docker-compose.yml
├── start.sh
├── .env.example
└── README.md
```

## Quick start (local)

```bash
cp .env.example .env
# Edit .env — set GOOGLE_API_KEY (or GROQ_API_KEY)

chmod +x start.sh
./start.sh
```

Open **http://127.0.0.1:5173** — no login required.

API health: **http://127.0.0.1:8000/health**

## Docker

```bash
cp .env.example .env
docker compose up --build
```

- Frontend: http://localhost:5173
- Backend: http://localhost:8000

## Production images

```bash
docker build -t sttm-backend:latest ./backend
docker build -t sttm-frontend:latest --target production \
  --build-arg VITE_API_BASE_URL=https://your-api.example.com \
  ./frontend
```

Set `DATAMAP_CORS_ORIGINS` on the backend to your frontend URL.

## Environment variables

| Variable | Purpose |
|----------|---------|
| `GOOGLE_API_KEY` | Gemini / ADK agents |
| `GROQ_API_KEY` | Alternative LLM (`LLM_PROVIDER=groq`) |
| `APP_SESSION_AUTH_MODE` | `dev` (no login; fixed `local-user`) |
| `DATAMAP_CORS_ORIGINS` | Allowed frontend origin(s) |
| `VITE_API_BASE_URL` | Frontend → API URL (production build) |

## Auth

No login or SSO. All app sessions are scoped to a fixed backend identity (`local-user`). Suitable for single-team / trusted-network deployments.

## Export

Copy the whole `sttm-standalone/` directory to a new repo or server. No dependency on the parent Launchpad project.
