"""Shared test fixtures + env defaults so settings load without a real .env."""
from __future__ import annotations

import os

# Set test env vars BEFORE app modules import settings.
os.environ.setdefault("APP_ENV", "development")
os.environ.setdefault("ENCRYPTION_KEY", "dGVzdC1lbmNyeXB0aW9uLWtleS1mb3ItdW5pdC10ZXN0cw==")
os.environ.setdefault("SECRET_KEY", "test-secret")
os.environ.setdefault(
    "DATABASE_URL", "postgresql+asyncpg://polier:polier@localhost:5432/polier"
)
os.environ.setdefault(
    "DATABASE_URL_SYNC", "postgresql+psycopg2://polier:polier@localhost:5432/polier"
)
os.environ.setdefault("REDIS_URL", "redis://localhost:6379/0")
os.environ.setdefault("TWILIO_ACCOUNT_SID", "ACtestaccountsidplaceholder1234567")
os.environ.setdefault("TWILIO_AUTH_TOKEN", "test-token")
os.environ.setdefault("TWILIO_WEBHOOK_VALIDATE", "false")
os.environ.setdefault("OPENAI_API_KEY", "sk-test-placeholder")
os.environ.setdefault("ANTHROPIC_API_KEY", "sk-ant-test-placeholder")
os.environ.setdefault("STORAGE_BACKEND", "local")
os.environ.setdefault("STORAGE_LOCAL_PATH", "/tmp/polier-test-storage")
