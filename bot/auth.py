"""
Run this script ONCE to authenticate with Telegram and get a session string.
After running, copy the session string and save it as TELEGRAM_SESSION environment variable.

Usage:
    python bot/auth.py
"""
import asyncio
import os
from telethon import TelegramClient
from telethon.sessions import StringSession


async def main():
    api_id = int(os.environ.get("TELEGRAM_API_ID", "") or input("Enter API ID: "))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "") or input("Enter API Hash: ")
    phone = os.environ.get("TELEGRAM_PHONE", "") or input("Enter phone (+66...): ")

    print(f"\nConnecting to Telegram with phone: {phone}")
    client = TelegramClient(StringSession(), api_id, api_hash)
    await client.start(phone=phone)

    session_string = client.session.save()

    print("\n" + "=" * 60)
    print("SUCCESS! Save this as TELEGRAM_SESSION environment variable:")
    print("=" * 60)
    print(session_string)
    print("=" * 60)
    print("\nOn Railway: Settings → Variables → Add TELEGRAM_SESSION = <above string>")

    me = await client.get_me()
    print(f"\nLogged in as: {me.first_name} (@{me.username})")
    await client.disconnect()


if __name__ == "__main__":
    asyncio.run(main())
