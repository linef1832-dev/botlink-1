import asyncio
import re
import os
import json
import logging
from datetime import datetime

from telethon import TelegramClient, events
from telethon.sessions import StringSession
import discord
from discord import ChannelType
import aiohttp

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Employee mapping  (Telegram ID -> employee info)
# ---------------------------------------------------------------------------
EMPLOYEES: dict[str, dict] = {
    "8046888446": {"name": "PRIDE", "discord_id": "937663612766548028"},
    "8204537651": {"name": "YAO", "discord_id": "1438396440861872168"},
    "7194888933": {"name": "RHAM", "discord_id": "855213538247573544"},
    "6425316652": {"name": "ALIEN", "discord_id": "741711562120888350"},
    "8492341073": {"name": "ALLNEW", "discord_id": "1447830227106402446"},
    "8380767244": {"name": "NIDA", "discord_id": "1458699656874950782"},
    "6750457815": {"name": "MEMORIES", "discord_id": "1506253460176109709"},
    "8456635697": {"name": "BAMBI", "discord_id": "1458707432300609566"},
    "7849459391": {"name": "PRAI", "discord_id": "1459021946854572075"},
    "6988453021": {"name": "YOUNGJAY", "discord_id": "875611878473228388"},
    "5554617607": {"name": "CHICKS", "discord_id": "1081857927733842030"},
    "5623769505": {"name": "RIKA", "discord_id": "892552753270911018"},
    "6073022140": {"name": "TRUMP", "discord_id": "1377551300123689010"},
    "8560735759": {"name": "KWANCHAI", "discord_id": "1468270550626599127"},
    "8502456217": {"name": "KEVIN", "discord_id": "1463020827216973894"},
    "8911792172": {"name": "THEE", "discord_id": "1384210089006600376"},
    "6578592217": {"name": "LYDIA", "discord_id": "1463041919587193026"},
    "6582978944": {"name": "PALO", "discord_id": "1434864108766756994"},
    "7235598775": {"name": "BEAM", "discord_id": "1474438080470585395"},
    "8731963427": {"name": "REVO", "discord_id": "1451093813534920750"},
    "7554839486": {"name": "FOLEN", "discord_id": "1463021218171981953"},
    "7707044062": {"name": "BULEE", "discord_id": "1461997174043709556"},
    "8528198622": {"name": "DAYONE", "discord_id": "1470439159574560869"},
    "8103612072": {"name": "MEBEL", "discord_id": "1472799216488288358"},
    "7607162067": {"name": "POOKPIK", "discord_id": "1471087361046876215"},
    "7775151875": {"name": "ANAN", "discord_id": "1468844740261842976"},
    "8158661621": {"name": "CHIN", "discord_id": "1493397604799611040"},
    "8527879554": {"name": "LUKKAI", "discord_id": "1467780092108476416"},
    "8225463050": {"name": "NIMMAN", "discord_id": "1466300492819726439"},
    "7681502019": {"name": "PIRIYA", "discord_id": "1304037473915113546"},
    "6275536589": {"name": "ARYA", "discord_id": "1445125175946514649"},
    "6635722903": {"name": "XANDER", "discord_id": "1477190188592529672"},
    "7658174553": {"name": "MARIN", "discord_id": "1472073717202026538"},
    "8585155045": {"name": "GABRIAN", "discord_id": "699888907126308906"},
    "8599131396": {"name": "BREAD", "discord_id": "1475059204149612595"},
    "8362535494": {"name": "DAX", "discord_id": "1456983093922496523"},
    "8438010552": {"name": "SAKURA", "discord_id": "1476973201631219804"},
    "8592723303": {"name": "BASS", "discord_id": "1497720055121576020"},
    "7453498643": {"name": "GOBGAP", "discord_id": "1472898506891988992"},
    "8759871424": {"name": "PRANG", "discord_id": "1482279911967297611"},
    "1368117045": {"name": "KELLY", "discord_id": "721037014388178964"},
    "8392745693": {"name": "PHIM", "discord_id": "1481249177408372738"},
    "5662320070": {"name": "DESTA", "discord_id": "1383057863173214300"},
    "7858728622": {"name": "MONGKEY", "discord_id": "1403604944061468854"},
    "7465321328": {"name": "MELIE", "discord_id": "1477012122197295246"},
    "6402453881": {"name": "SURI", "discord_id": "1451205039954854060"},
    "8723574609": {"name": "SCARLET", "discord_id": "1494629820271825016"},
    "8669550253": {"name": "SUSY", "discord_id": "1493980177892638790"},
    "8840483162": {"name": "WISDOM", "discord_id": "835118253931233310"},
    "8506336451": {"name": "ZYREN", "discord_id": "1481070139058163878"},
    "8761505054": {"name": "BLACKCAT", "discord_id": "1501072565970669598"},
    "8761919464": {"name": "ZEUS", "discord_id": "1434919983435223101"},
    "8729754073": {"name": "MIKAEL", "discord_id": "759280255381798962"},
    "8640787369": {"name": "JAPAO", "discord_id": "1497389449825226793"},
    "7552569589": {"name": "YAIMAI", "discord_id": "1483412107956453457"},
    "8218127334": {"name": "JULIE", "discord_id": "1483461742510997506"},
    "8494681520": {"name": "NYE", "discord_id": "1483866077187543061"},
    "8270835449": {"name": "TAEYANG", "discord_id": "1484070985438331022"},
    "8331438797": {"name": "MAWIN", "discord_id": "1483379690101280830"},
    "8330944910": {"name": "NADA", "discord_id": "1464870423106945024"},
    "8094793794": {"name": "ROGER", "discord_id": "885721870488436736"},
    "7963274593": {"name": "MEIFERN", "discord_id": "1451094885678579763"},
    "5912256964": {"name": "SLOBE", "discord_id": "1452589286548308028"},
    "8321818445": {"name": "KUMA", "discord_id": "280643046067142656"},
    "5561904262": {"name": "THINGTHING", "discord_id": "1122962399910170814"},
    "8385516985": {"name": "LUZY", "discord_id": "690461334504210432"},
    "5477060253": {"name": "ENGENE", "discord_id": "1473637594657980570"},
    "8155166757": {"name": "RUBSARB", "discord_id": "465160716698124319"},
    "7421340881": {"name": "SEEN", "discord_id": "1457281901952761898"},
    "6436209710": {"name": "MARYM", "discord_id": "884763024286691398"},
    "8385283326": {"name": "HIACHANG", "discord_id": "1258636317210968064"},
    "8588789702": {"name": "NUTZA", "discord_id": "1462116752875065515"},
    "8519049805": {"name": "PUKAN", "discord_id": "1136833359222419517"},
    "6179092946": {"name": "RAREEN", "discord_id": "1471799431803310080"},
    "7991808096": {"name": "MAGAN", "discord_id": "1418194553734959214"},
    "6396414761": {"name": "AWANG", "discord_id": "1461256969179365400"},
    "8512234523": {"name": "GWEN", "discord_id": "1093955667431280784"},
    "8280442513": {"name": "BEENA", "discord_id": "1184129991106121829"},
    "7104118055": {"name": "PIMMI", "discord_id": "606682935155884032"},
    "8366504139": {"name": "MICKEY", "discord_id": "638352634994098186"},
    "8452781349": {"name": "FONGJIN", "discord_id": "1486075312272838920"},
    "8583331973": {"name": "NADOL", "discord_id": "1470364104203047141"},
    "8243789592": {"name": "WENDY", "discord_id": "736063999539544105"},
    "7720725242": {"name": "MAPRAW", "discord_id": "1237461033539473428"},
    "7086818811": {"name": "FRING", "discord_id": "1470601644013125773"},
    "6908609234": {"name": "DAJIM", "discord_id": "762999364917526539"},
    "8052140335": {"name": "GIPZY", "discord_id": "1466047478427160620"},
    "7228474384": {"name": "LOST", "discord_id": "1199240280679927818"},
    "8577888334": {"name": "VIEW", "discord_id": "1468499147467259995"},
    "8550887286": {"name": "TEEMEE", "discord_id": "1472052848467644416"},
    "5468698254": {"name": "MILO", "discord_id": "1338856952503209994"},
    "8348178547": {"name": "GARETH", "discord_id": "1253902663180091454"},
    "8594743671": {"name": "DANA", "discord_id": "1334803952725786660"},
    "6855153960": {"name": "CREAMME", "discord_id": "1143353948581924884"},
    "8366965168": {"name": "ROMAN", "discord_id": "1127340978899001494"},
    "5918754528": {"name": "XAOIBAI", "discord_id": "1433015221819019286"},
    "8553244404": {"name": "NINJA", "discord_id": "1475375983703097386"},
    "6773776173": {"name": "KOKO", "discord_id": "1474328897389592690"},
    "7569122247": {"name": "TONPHAI", "discord_id": "1298736039136858164"},
    "8299349977": {"name": "DISNEY", "discord_id": "1476069704190922824"},
    "7780234618": {"name": "KAIA", "discord_id": "1474048953124388895"},
    "7535232991": {"name": "PIKUL", "discord_id": "1460652066455158819"},
    "8530375236": {"name": "FREAK", "discord_id": "1478672600132751483"},
    "5928986894": {"name": "MABOO", "discord_id": "1482458538235138215"},
    "8478420677": {"name": "DRINK", "discord_id": "1463086389208813724"},
    "8612517477": {"name": "SPACE", "discord_id": "1494184627110416425"},
    "8585437393": {"name": "PUNPUN", "discord_id": "1494564037738889349"},
    "8143722666": {"name": "TEDDY", "discord_id": "783553980781035541"},
    "6912075391": {"name": "AOFFY", "discord_id": "846977903748317214"},
    "6392523761": {"name": "KAIJAEW", "discord_id": "1306505294871793685"},
    "6558672538": {"name": "TURBO", "discord_id": "965646273690632223"},
    "8904558738": {"name": "FUMI", "discord_id": "1385301261754306631"},
    "8712544949": {"name": "PHURI", "discord_id": "1227619511956541440"},
    "6774588340": {"name": "PRIEWPRIEW", "discord_id": "1485581872904798230"},
    "7059861131": {"name": "GRACIE", "discord_id": "1499298891861065791"},
    "7282670765": {"name": "CAMP", "discord_id": "1482275631537586247"},
    "8629386763": {"name": "TAMMY", "discord_id": "1483866155306455061"},
    "7980323307": {"name": "CALI", "discord_id": "1483348961942306877"},
    "8467818809": {"name": "TOR", "discord_id": "1483495290777501839"},
    "8310418152": {"name": "CHABA", "discord_id": "1481000364391010451"},
    "8254737736": {"name": "BAILAY", "discord_id": "1386249982318870528"},
    "7708086148": {"name": "THAI", "discord_id": "1483307641844269236"},
    "7358066222": {"name": "REBRON", "discord_id": "1508636898904506489"},
}

TARGET_GROUPS = ["Jun88"]

RETURN_KEYWORDS = ["กลับที่นั่ง", "กลับที่นัง", "回座"]

ACTIVITY_EMOJI = {
    "กินข้าว": "🍚", "ทานข้าว": "🍚",
    "ปวดหนัก": "🚽", "ห้องน้ำใหญ่": "🚽",
    "ปวดน้อย": "🚾", "ห้องน้ำเล็ก": "🚾",
    "พัก": "☕",
}


def get_emoji(activity: str, is_return: bool) -> str:
    if is_return:
        return "✅"
    for key, emoji in ACTIVITY_EMOJI.items():
        if key in activity:
            return emoji
    return "🚶"


def parse_message(text: str, group_name: str) -> dict | None:
    if not text:
        return None

    # Skip error/failed messages (❌ with ไม่สามารถ)
    if "❌" in text and ("ไม่สามารถ" in text or "失败" in text):
        return None

    # Extract Telegram ID — supports both plain and markdown-wrapped:
    # "รหัสผู้ใช้：12345"  or  "**รหัสผู้ใช้：** `12345`"
    tid_match = re.search(r"รหัสผู้ใช้[：:][^\d]*(\d+)", text)
    if not tid_match:
        return None

    activity = None

    # Format A: check-in success  "✅ **ลงทะเบียนสำเร็จ：** `ปวดหนัก`"
    a_match = re.search(r"ลงทะเบียนสำเร็จ[：:][^`]*`([^`]+)`", text)
    if a_match:
        activity = a_match.group(1).strip()

    # Format B: กลับที่นั่ง  "ลงทะเบียนสำหรับ กลับที่นั่ง สำเร็จ"
    if not activity:
        b_match = re.search(r"ลงทะเบียนสำหรับ\s+(.+?)\s+สำเร็จ", text)
        if b_match:
            activity = b_match.group(1).strip()

    # Format C: plain  "ลงทะเบียนสำหรับ <activity>" without สำเร็จ
    if not activity:
        c_match = re.search(r"ลงทะเบียนสำหรับ\s+(\S+)", text)
        if c_match:
            activity = c_match.group(1).strip()

    if not activity:
        return None

    # Strip location suffix e.g. "ปวดน้อย.สูบบุหรี่" → "ปวดน้อย"
    activity = activity.split(".")[0].strip()

    is_return = any(kw in activity for kw in RETURN_KEYWORDS)

    ts_match = re.search(r"(\d{2}/\d{2}\s+\d{2}:\d{2}:\d{2})", text)
    timestamp = ts_match.group(1) if ts_match else datetime.now().strftime("%d/%m %H:%M:%S")

    dur_match = re.search(r"เวลากิจกรรมนี้[：:][^`]*`([^`]+)`", text)
    duration = dur_match.group(1) if dur_match else None

    return {
        "telegram_id": tid_match.group(1),
        "activity": "กลับที่นั่ง" if is_return else activity,
        "is_return": is_return,
        "timestamp": timestamp,
        "duration": duration,
        "group_name": group_name,
    }


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------
class ActivityBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self._ready_event = asyncio.Event()

    async def on_ready(self):
        logger.info(f"Discord bot ready: {self.user}")
        self._ready_event.set()

    async def wait_until_ready_event(self):
        await self._ready_event.wait()

    async def find_text_channel_for_member(self, discord_user_id: str) -> discord.TextChannel | None:
        for guild in self.guilds:
            try:
                member = guild.get_member(int(discord_user_id))
                if member is None:
                    member = await guild.fetch_member(int(discord_user_id))
            except Exception:
                continue

            if member is None:
                continue

            voice = member.voice
            if not voice or not voice.channel:
                logger.info(f"{discord_user_id} not in voice channel")
                return None

            vc = voice.channel
            logger.info(f"Found {discord_user_id} in voice channel: {vc.name}")

            # Try text channel in same category
            if vc.category:
                for ch in vc.category.channels:
                    if isinstance(ch, discord.TextChannel):
                        return ch

            # Try text channel with similar name
            for ch in guild.text_channels:
                if vc.name.lower()[:5] in ch.name.lower():
                    return ch

        return None

    async def send_notification(self, discord_user_id: str, name: str, activity: str,
                                 is_return: bool, group_name: str) -> tuple[bool, str | None]:
        emoji = get_emoji(activity, is_return)
        action = f"**{name}** กลับที่นั่งแล้ว" if is_return else f"**{name}** ไป{activity}"
        now = datetime.now().strftime("%H:%M")
        message = f"{emoji} {action}\n> 🕐 {now} · 📌 {group_name}"

        channel = await self.find_text_channel_for_member(discord_user_id)
        if channel:
            try:
                await channel.send(message)
                logger.info(f"Sent to #{channel.name}: {name} - {activity}")
                return True, channel.name
            except Exception as e:
                logger.error(f"Failed to send to #{channel.name}: {e}")

        logger.warning(f"No channel found for {name} ({discord_user_id})")
        return False, None


discord_bot = ActivityBot()


# ---------------------------------------------------------------------------
# Telegram userbot
# ---------------------------------------------------------------------------
async def start_telegram(on_activity):
    api_id = int(os.environ.get("TELEGRAM_API_ID", "0"))
    api_hash = os.environ.get("TELEGRAM_API_HASH", "")
    phone = os.environ.get("TELEGRAM_PHONE", "")
    session_str = os.environ.get("TELEGRAM_SESSION", "")

    if not api_id or not api_hash or not phone:
        logger.warning("Missing TELEGRAM_API_ID / TELEGRAM_API_HASH / TELEGRAM_PHONE. Telegram bot disabled.")
        return

    if not session_str:
        logger.error(
            "TELEGRAM_SESSION is not set. "
            "Run 'python bot/auth.py' in the Shell to authenticate and get the session string, "
            "then save it as TELEGRAM_SESSION environment variable."
        )
        return

    session = StringSession(session_str)
    client = TelegramClient(session, api_id, api_hash)

    await client.start(phone=phone)

    saved = client.session.save()
    if saved != session_str:
        logger.info("=" * 60)
        logger.info("NEW SESSION STRING — save as TELEGRAM_SESSION secret:")
        logger.info(saved)
        logger.info("=" * 60)

    me = await client.get_me()
    logger.info(f"Telegram connected as: {me.username or me.first_name}")

    @client.on(events.NewMessage())
    async def handler(event):
        try:
            chat = await event.get_chat()
            title = getattr(chat, "title", "") or ""

            # Debug: log every message from Jun88 groups
            if any(g in title for g in TARGET_GROUPS):
                text = event.message.text or ""
                sender = getattr(event.message.sender, "username", None) or getattr(event.message.sender, "first_name", "unknown") if event.message.sender else "unknown"
                logger.info(f"[{title}] msg from {sender}: {text[:120]!r}")

                parsed = parse_message(text, title)
                if parsed:
                    logger.info(f"Activity detected: {parsed['telegram_id']} - {parsed['activity']}")
                    await on_activity(parsed)
                elif "รหัสผู้ใช้" in text:
                    logger.warning(f"Has รหัสผู้ใช้ but parse failed. Full text: {text!r}")
            else:
                pass  # Ignore non-Jun88 groups silently
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}")

    logger.info(f"Listening for groups containing: {TARGET_GROUPS}")
    await client.run_until_disconnected()


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------
async def on_activity(parsed: dict):
    emp = EMPLOYEES.get(parsed["telegram_id"])
    if not emp:
        logger.warning(f"Unknown Telegram ID: {parsed['telegram_id']}")
        return

    await discord_bot.wait_until_ready_event()
    sent, channel = await discord_bot.send_notification(
        emp["discord_id"],
        emp["name"],
        parsed["activity"],
        parsed["is_return"],
        parsed["group_name"],
    )
    status = f"sent to #{channel}" if sent else "not sent (not in voice channel)"
    logger.info(f"{emp['name']} [{parsed['activity']}] → Discord {status}")


async def main():
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not discord_token:
        logger.warning("Missing DISCORD_BOT_TOKEN. Discord bot disabled.")
        discord_task = asyncio.create_task(asyncio.sleep(0))
    else:
        discord_task = asyncio.create_task(discord_bot.start(discord_token))

    telegram_task = asyncio.create_task(start_telegram(on_activity))

    await asyncio.gather(discord_task, telegram_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
