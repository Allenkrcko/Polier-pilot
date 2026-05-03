"""Tiny Telegram echo bot - the simplest possible proof of life.

Connects to Telegram via long-polling (no public URL, no webhook, no DB,
no Docker). Run from anywhere with Python + internet:

    pip install httpx
    TELEGRAM_BOT_TOKEN=12345:ABCdef python scripts/echo_bot.py

Send any message to your bot from the phone -> see it echoed back.

Once this works, you know:
  - your bot token is valid
  - Telegram can reach you (no firewall issues)
  - your phone is talking to the right bot

Then the full Polier-Pilot stack (Postgres + Redis + AI services) can
be wired in. This script intentionally has zero project dependencies.
"""
from __future__ import annotations

import asyncio
import logging
import os
import sys

import httpx

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(message)s")
log = logging.getLogger("echo_bot")


async def main() -> None:
    token = os.environ.get("TELEGRAM_BOT_TOKEN")
    if not token:
        sys.exit("Set TELEGRAM_BOT_TOKEN env var first.")

    base = f"https://api.telegram.org/bot{token}"

    async with httpx.AsyncClient(timeout=httpx.Timeout(60.0, connect=10.0)) as client:
        # Sanity check the token before entering the long-poll loop.
        me = (await client.get(f"{base}/getMe")).json()
        if not me.get("ok"):
            sys.exit(f"Bad token: {me}")
        log.info("Connected as @%s — send any message from your phone.", me["result"]["username"])

        # Drop any previously registered webhook so getUpdates is allowed to
        # return updates (Telegram refuses both modes simultaneously).
        await client.post(f"{base}/deleteWebhook", json={"drop_pending_updates": True})

        offset = 0
        while True:
            try:
                resp = await client.get(
                    f"{base}/getUpdates",
                    params={"offset": offset, "timeout": 30},
                )
                body = resp.json()
            except httpx.ReadTimeout:
                continue
            except Exception:  # noqa: BLE001
                log.exception("poll failed; sleeping 3s")
                await asyncio.sleep(3)
                continue

            for upd in body.get("result", []):
                offset = upd["update_id"] + 1
                msg = upd.get("message") or upd.get("edited_message")
                if not msg:
                    continue

                chat_id = msg["chat"]["id"]
                first = (msg.get("from") or {}).get("first_name", "Polier")

                if msg.get("voice") or msg.get("audio"):
                    payload = "🎙️ Sprachnachricht empfangen."
                elif msg.get("photo"):
                    payload = "📸 Foto empfangen."
                elif msg.get("document"):
                    payload = f"📎 Dokument empfangen: {msg['document'].get('file_name')}"
                elif msg.get("video") or msg.get("video_note"):
                    payload = "🎥 Video empfangen."
                elif (text := msg.get("text")):
                    payload = "💬 Text empfangen: " + text
                else:
                    payload = "Nachricht empfangen."

                reply = f"Hallo {first}! {payload}\n\n(Polier-Pilot Echo-Bot — die volle KI-Verarbeitung folgt.)"

                send = await client.post(
                    f"{base}/sendMessage",
                    json={"chat_id": chat_id, "text": reply},
                )
                if send.status_code == 200:
                    log.info("replied to chat=%s: %s", chat_id, payload)
                else:
                    log.warning("send failed %d: %s", send.status_code, send.text)


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("\nBye.")
