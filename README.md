# Polier-Pilot

AI-first construction site reporting tool for German FTTH/Tiefbau companies.
Foremen ("Polier") send WhatsApp voice memos and photos in their native language;
the system auto-generates legally compliant German construction reports
(Bautagebuch, Aufmaß, Mängelliste) per VOB/B §6.

> **Status:** Phase 1 (Foundation) — webhook persists raw messages.
> Transcription + report generation ship in later phases.

---

## Stack

| Layer | Choice |
|------|--------|
| API | FastAPI (Python 3.11+) |
| DB | PostgreSQL 16 + SQLAlchemy 2.0 async |
| Queue | Redis 7 + RQ |
| WhatsApp | Twilio Business API |
| STT | OpenAI Whisper (`whisper-1`) |
| LLM | Anthropic Claude (`claude-sonnet-4-5` + `claude-haiku-4-5`) |
| PDF | WeasyPrint |
| Hosting | Hetzner Cloud, Docker Compose, German data residency |

---

## Quickstart (10 steps)

> Requires Docker Desktop or Docker Engine 24+, plus Compose v2.

1. **Clone the repo**
   ```bash
   git clone <repo-url> polier-pilot && cd polier-pilot
   ```

2. **Create your `.env`** from the template
   ```bash
   cp .env.example .env
   ```

3. **Generate an encryption key** and paste it into `.env` as `ENCRYPTION_KEY`
   ```bash
   docker run --rm python:3.11-slim python -c \
     "from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())"
   ```

4. **Fill in real API keys** in `.env`:
   - `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN` — from console.twilio.com
   - `OPENAI_API_KEY` — from platform.openai.com
   - `ANTHROPIC_API_KEY` — from console.anthropic.com

5. **Build + start the stack**
   ```bash
   docker compose up -d --build
   ```
   Brings up `postgres`, `redis`, `app` (FastAPI on :8000), and `worker` (RQ).

6. **Run database migrations**
   ```bash
   docker compose exec app alembic upgrade head
   ```

7. **Verify the stack is healthy**
   ```bash
   curl http://localhost:8000/healthz
   # {"status":"ok","checks":{"app":"ok","database":"ok","redis":"ok"}}
   ```

8. **Browse the OpenAPI docs**

   Open <http://localhost:8000/docs> in your browser.

9. **Expose the webhook to Twilio** (sandbox testing) using ngrok:
   ```bash
   ngrok http 8000
   ```
   In the Twilio WhatsApp sandbox, set the inbound webhook to
   `https://<ngrok-id>.ngrok.io/webhooks/twilio` (POST).

10. **Send a WhatsApp message** to your sandbox number. The webhook persists
    a row in `messages`. To skip Twilio signature checks for local `curl`
    tests, set `TWILIO_WEBHOOK_VALIDATE=false` in `.env` and restart `app`.

---

## Project layout

```
polier-pilot/
├── app/
│   ├── main.py              # FastAPI entry + /healthz
│   ├── config.py            # Pydantic settings (reads .env)
│   ├── logging_config.py    # PII-scrubbing logger
│   ├── api/
│   │   └── webhooks.py      # POST /webhooks/twilio
│   └── db/
│       ├── base.py          # DeclarativeBase + UUIDPrimaryKey, TimestampMixin
│       ├── session.py       # async engine, SessionLocal, get_db
│       └── models/          # Company, Project, User, Message, Photo,
│                            # ReportSession, Report
├── alembic/                 # Migrations
├── docker-compose.yml       # postgres + redis + app + worker
├── Dockerfile
├── pyproject.toml
└── .env.example
```

---

## Useful commands

```bash
# Tail app logs
docker compose logs -f app

# Open a Python shell with app context
docker compose exec app python

# Create a new migration after model changes
docker compose exec app alembic revision --autogenerate -m "describe change"

# Apply migrations
docker compose exec app alembic upgrade head

# Run tests (after Phase 2)
docker compose exec app pytest

# Drop everything (DESTROYS DATA)
docker compose down -v
```

---

## Environment variables

See [`.env.example`](./.env.example) for the full list. Required for Phase 1:

| Var | Purpose |
|-----|---------|
| `DATABASE_URL` | async DSN (`postgresql+asyncpg://…`) |
| `DATABASE_URL_SYNC` | sync DSN for Alembic (`postgresql+psycopg2://…`) |
| `REDIS_URL` | RQ + cache |
| `TWILIO_ACCOUNT_SID` / `TWILIO_AUTH_TOKEN` | webhook signature validation |
| `OPENAI_API_KEY` | Whisper STT (Phase 2) |
| `ANTHROPIC_API_KEY` | Claude LLM (Phase 3+) |
| `ENCRYPTION_KEY` | Fernet key for media-at-rest encryption |
| `SECRET_KEY` | random secret used for app-internal signing |

---

## DSGVO / data protection

- All data residency in Germany (Hetzner Cloud, Frankfurt/Nürnberg).
- Raw audio auto-deleted after `RAW_AUDIO_RETENTION_DAYS` (default 90 days).
- Reports retained for `REPORT_RETENTION_DAYS` (default 730 = 2 years per VOB).
- Logs run through a PII scrubber (phone numbers, API keys masked).
- Twilio webhook signature validated on every request when
  `TWILIO_WEBHOOK_VALIDATE=true` (default).

---

## Roadmap

- **Phase 1 (current)** — scaffolding, webhook persists raw messages.
- **Phase 2** — RQ workers, Whisper transcription, reply confirmations.
- **Phase 3** — Claude Haiku intent classification, ReportSession aggregation,
  EXIF/GPS, Open-Meteo weather enrichment.
- **Phase 4** — Bautagebuch generation (Claude Sonnet → JSON → WeasyPrint PDF),
  end-of-day "Feierabend" trigger, WhatsApp confirmation flow.
- **Phase 5** — Aufmaß + Mängelliste reports.
- **Phase 6** — SMTP fan-out to Bauleiter / GF.
- **Phase 7+** — billing, multi-tenancy UI, SAP/DATEV integration.

---

## License

Proprietary. © Polier-Pilot.
