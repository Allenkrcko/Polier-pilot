"""Register or inspect the Telegram webhook.

Usage (from the project root):

  # Register the webhook against a public URL:
  .venv/bin/python -m scripts.telegram_setwebhook \
      --url https://abc123.ngrok.io/webhooks/telegram

  # Show the current webhook info:
  .venv/bin/python -m scripts.telegram_setwebhook --info

  # Remove the webhook (e.g. before switching tunnels):
  .venv/bin/python -m scripts.telegram_setwebhook --delete

The bot token comes from TELEGRAM_BOT_TOKEN; the secret comes from
TELEGRAM_WEBHOOK_SECRET. Both must already be set in .env.
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys

import httpx

from app.config import settings


def _api_url(method: str) -> str:
    if settings.telegram_bot_token is None:
        raise SystemExit("TELEGRAM_BOT_TOKEN is not set in .env")
    return f"https://api.telegram.org/bot{settings.telegram_bot_token.get_secret_value()}/{method}"


async def _set(url: str) -> None:
    secret = (
        settings.telegram_webhook_secret.get_secret_value()
        if settings.telegram_webhook_secret
        else None
    )
    payload: dict[str, object] = {
        "url": url,
        "allowed_updates": ["message", "edited_message"],
        "drop_pending_updates": True,
    }
    if secret:
        payload["secret_token"] = secret

    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_api_url("setWebhook"), json=payload)
    print(f"setWebhook -> {resp.status_code}")
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))


async def _info() -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_api_url("getWebhookInfo"))
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))


async def _delete() -> None:
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.post(_api_url("deleteWebhook"))
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))


async def _me() -> None:
    """Useful smoke check: confirm the token is valid."""
    async with httpx.AsyncClient(timeout=15) as client:
        resp = await client.get(_api_url("getMe"))
    print(json.dumps(resp.json(), indent=2, ensure_ascii=False))


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    g = parser.add_mutually_exclusive_group(required=True)
    g.add_argument("--url", help="Public URL of /webhooks/telegram")
    g.add_argument("--info", action="store_true", help="Show current webhook info")
    g.add_argument("--delete", action="store_true", help="Delete the webhook")
    g.add_argument("--me", action="store_true", help="Test the bot token via getMe")
    args = parser.parse_args()

    if args.url:
        asyncio.run(_set(args.url))
    elif args.info:
        asyncio.run(_info())
    elif args.delete:
        asyncio.run(_delete())
    elif args.me:
        asyncio.run(_me())
    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()
