import asyncio
import re
import os
import json
import logging
import time as time_module
from collections import defaultdict
from datetime import datetime

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError
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
    "8736200334": {"name": "FRING", "discord_id": "1470601644013125773"},
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
    "8790024904": {"name": "FIWWY", "discord_id": "1480817079258054689"},
    "8742150292": {"name": "RAPTOR", "discord_id": "1478312523374657557"},
    "8142690458": {"name": "BRANT", "discord_id": "1468420877325566094"},
    "8370430767": {"name": "FOLKE", "discord_id": "1501920596315865129"},
    "7790255430": {"name": "DEVIN", "discord_id": "1346757897492627476"},
    "8202207453": {"name": "PANTER", "discord_id": "1483818860502188182"},
    "5745637611": {"name": "SAKAI", "discord_id": "385473230384791562"},
    "5398437494": {"name": "DOLIA", "discord_id": "1385570608904933446"},
    "8520335906": {"name": "SARA", "discord_id": "1469903384919150659"},
    "5734088160": {"name": "DORIS", "discord_id": "1443548147140788298"},
    "5968527149": {"name": "AEM", "discord_id": "1481379556832313344"},
    "7487109657": {"name": "TINDER", "discord_id": "1340925092523151361"},
    "8179270865": {"name": "RAYNA", "discord_id": "735383998767038535"},
    "7734869356": {"name": "CHITA", "discord_id": "1473569307442286603"},
    "8528099488": {"name": "MIKA", "discord_id": "1445282308906291265"},
    "5679263927": {"name": "LAYLA", "discord_id": "1474297870327746582"},
    "8375637281": {"name": "CODEX", "discord_id": "944703805180756018"},
    "8193432362": {"name": "NANOON", "discord_id": "1473947581619896341"},
    "7433754403": {"name": "CAWAII", "discord_id": "1478260599380246719"},
    "5001503243": {"name": "FINALHELL", "discord_id": "226352705847689217"},
    "7270565060": {"name": "MOMO", "discord_id": "1475758468718788741"},
    "7904470714": {"name": "SEK", "discord_id": "1460468129057472585"},
    "7795611138": {"name": "AIBRO", "discord_id": "1490203560879587351"},
    "6511614629": {"name": "MOUSE", "discord_id": "1482630543060369408"},
    "2026291311": {"name": "MINI", "discord_id": "1352309695024726107"},
    "7033451658": {"name": "KANOMPANG", "discord_id": "1460165527937875993"},
    "8108697897": {"name": "STORM", "discord_id": "1477990611050303629"},
    "6764248570": {"name": "NAIROBI", "discord_id": "1278241526832697378"},
    "8397868763": {"name": "LANLING", "discord_id": "1469931328819695691"},
    "7944285544": {"name": "FUJI", "discord_id": "1469936153384976602"},
    "8392215042": {"name": "TOFFY", "discord_id": "1469923409759572079"},
    "7558089138": {"name": "NOOKER", "discord_id": "1469953407405264961"},
    "7002273351": {"name": "BRIAN", "discord_id": "1470634319272742994"},
    "5807524388": {"name": "COPTER", "discord_id": "1474295302298271908"},
    "8088282133": {"name": "MUNG", "discord_id": "976425793846665286"},
    "8144348662": {"name": "SALY", "discord_id": "1474631391152181258"},
    "7493380321": {"name": "POPSICLE", "discord_id": "1470771494987628711"},
    "5273594873": {"name": "PATTY", "discord_id": "726812488213200987"},
    "8114078368": {"name": "TAPAEW", "discord_id": "1473641812236308646"},
    "7788093508": {"name": "RORA", "discord_id": "1312973404764110879"},
    "8736633012": {"name": "LYLY", "discord_id": "1478096981976023173"},
    "7036734948": {"name": "HAYLEE", "discord_id": "1232669083195936799"},
    "8662504657": {"name": "AFEY", "discord_id": "1484560819973787680"},
    "6647194217": {"name": "POINT", "discord_id": "1483338333013807166"},
    "8588408697": {"name": "TANGO", "discord_id": "1483771517601841265"},
}

TARGET_GROUPS = ["Jun88-กลุ่มเช็คอิน打卡群", "Jun88-OL กลุ่มเช็คอิน 打卡群"]

RETURN_KEYWORDS = ["กลับที่นั่ง", "กลับที่นัง", "回座"]

ACTIVITY_EMOJI = {
    "กินข้าว": "🍚", "ทานข้าว": "🍚",
    "ปวดหนัก": "🚽", "ห้องน้ำใหญ่": "🚽",
    "ปวดน้อย": "🚾", "ห้องน้ำเล็ก": "🚾",
    "พัก": "☕",
}

# ---------------------------------------------------------------------------
# Activity → Discord voice channel auto-move
# Map activity keyword → Discord voice channel ID (as string)
# Leave empty string to disable auto-move for that activity
# ---------------------------------------------------------------------------
ACTIVITY_MOVE_CHANNEL: dict[str, str] = {
    "กินข้าว": "1451121950297686106",
    "ทานข้าว": "1451121950297686106",
    "พัก": "",
    "ปวดหนัก": "",
    "ปวดน้อย": "",
}

# ห้องที่ไม่นับเป็นห้องทำงาน (ห้องปลายทาง)
DESTINATION_CHANNEL_IDS: set[str] = {
    v for v in ACTIVITY_MOVE_CHANNEL.values() if v
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

    # Format A: check-in success with backticks  "✅ **ลงทะเบียนสำเร็จ：** `ปวดหนัก`"
    a_match = re.search(r"ลงทะเบียนสำเร็จ[：:][^`]*`([^`]+)`", text)
    if a_match:
        activity = a_match.group(1).strip()

    # Format A2: check-in success without backticks  "✅ ลงทะเบียนสำเร็จ：ปวดหนัก 06/09 16:14:07"
    if not activity:
        a2_match = re.search(r"ลงทะเบียนสำเร็จ[：:]\s*([^\s\d/][^\s\d/]*)", text)
        if a2_match:
            activity = a2_match.group(1).strip()

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
STATUS_CHANNEL_ID = int(os.environ.get("DISCORD_STATUS_CHANNEL_ID", "0") or "0")


class ActivityBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        super().__init__(intents=intents)
        self._ready_event = asyncio.Event()
        self.tree = discord.app_commands.CommandTree(self)

        # ระบบจำห้องทำงานแบบนับเวลา รายชั่วโมง
        # member_id → (channel_id, join_timestamp) — ห้องที่กำลังอยู่ตอนนี้
        self._voice_join_time: dict[int, tuple[int, float]] = {}
        # member_id → {channel_id: วินาทีที่อยู่} — สะสมไว้ตลอดชั่วโมงนี้
        self._hour_time: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        # ชั่วโมงปัจจุบัน (0-23) เพื่อรู้เมื่อขึ้นชั่วโมงใหม่
        self._current_hour: int = datetime.now().hour
        # member_id → channel_id ห้องที่แจ้งกิจกรรมล่าสุด (ใช้สำหรับ "กลับที่นั่ง")
        self._last_notified_channel: dict[int, int] = {}
        # member_id → discord.Message ของกิจกรรมล่าสุด (ใช้สำหรับ reply ตอนกลับที่นั่ง)
        self._last_activity_message: dict[int, discord.Message] = {}
        # member_id ที่ถูกย้ายไปห้องปลายทาง (เช่น Dining) — คนเหล่านี้เท่านั้นที่ต้องย้ายกลับ
        self._moved_to_destination: set[int] = set()

    async def setup_hook(self):
        await self.tree.sync()

    async def on_ready(self):
        logger.info(f"Discord bot ready: {self.user}")
        now = time_module.time()
        count = 0
        for guild in self.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if str(channel.id) not in DESTINATION_CHANNEL_IDS:
                        self._voice_join_time[member.id] = (channel.id, now)
                        count += 1
        if count:
            logger.info(f"Snapshot: found {count} member(s) already in voice channels")
        self._ready_event.set()

    async def send_status(self, message: str):
        """ส่งข้อความสถานะไปยัง DISCORD_STATUS_CHANNEL_ID ถ้ากำหนดไว้"""
        channel_id = os.environ.get("DISCORD_STATUS_CHANNEL_ID", "")
        if not channel_id:
            return
        try:
            channel = self.get_channel(int(channel_id))
            if channel:
                now = datetime.now().strftime("%d/%m %H:%M")
                await channel.send(f"{message}\n> 🕐 {now}")
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        now = time_module.time()
        current_hour = datetime.now().hour

        # ขึ้นชั่วโมงใหม่ → reset สถิติทั้งหมด
        if current_hour != self._current_hour:
            self._current_hour = current_hour
            self._hour_time.clear()
            logger.info(f"New hour slot {current_hour:02d}:00 — resetting work channel stats")

        # บันทึกเวลาที่อยู่ในห้องก่อนหน้า
        if member.id in self._voice_join_time:
            prev_ch_id, join_ts = self._voice_join_time[member.id]
            duration = now - join_ts
            if str(prev_ch_id) not in DESTINATION_CHANNEL_IDS:
                self._hour_time[member.id][prev_ch_id] += duration

        # บันทึกห้องใหม่ที่เข้า
        if after.channel:
            self._voice_join_time[member.id] = (after.channel.id, now)
        else:
            self._voice_join_time.pop(member.id, None)

    async def wait_until_ready_event(self):
        await self._ready_event.wait()

    async def find_member(self, discord_user_id: str) -> tuple[discord.Guild, discord.Member] | tuple[None, None]:
        for guild in self.guilds:
            try:
                member = guild.get_member(int(discord_user_id))
                if member is None:
                    member = await guild.fetch_member(int(discord_user_id))
                if member:
                    return guild, member
            except Exception:
                continue
        return None, None

    async def find_voice_channel_by_id(self, channel_id: str) -> discord.VoiceChannel | None:
        try:
            channel = self.get_channel(int(channel_id))
            if isinstance(channel, discord.VoiceChannel):
                return channel
        except Exception as e:
            logger.error(f"Failed to find channel by ID {channel_id}: {e}")
        return None

    async def find_voice_channel_for_member(self, discord_user_id: str) -> discord.VoiceChannel | None:
        _, member = await self.find_member(discord_user_id)
        if member is None:
            return None
        voice = member.voice
        if not voice or not voice.channel:
            logger.info(f"{discord_user_id} not in any voice channel")
            return None
        vc = voice.channel
        logger.info(f"Found {discord_user_id} in voice channel: '{vc.name}'")
        return vc

    def get_target_channel_name(self, activity: str) -> str:
        for keyword, channel_name in ACTIVITY_MOVE_CHANNEL.items():
            if keyword in activity:
                return channel_name
        return ""

    def get_work_channel_id(self, member_id: int) -> int | None:
        """คืน channel_id ที่อยู่นานสุดในชั่วโมงนี้ (ไม่นับห้องปลายทาง)"""
        now = time_module.time()

        # รวมเวลาที่สะสมไว้ + session ที่กำลังอยู่ตอนนี้
        time_by_ch: dict[int, float] = dict(self._hour_time[member_id])
        if member_id in self._voice_join_time:
            cur_ch_id, join_ts = self._voice_join_time[member_id]
            if str(cur_ch_id) not in DESTINATION_CHANNEL_IDS:
                time_by_ch[cur_ch_id] = time_by_ch.get(cur_ch_id, 0) + (now - join_ts)

        if not time_by_ch:
            return None
        return max(time_by_ch, key=lambda ch: time_by_ch[ch])

    async def send_notification(self, discord_user_id: str, name: str, activity: str,
                                 is_return: bool, group_name: str, timestamp: str | None = None) -> tuple[bool, str | None]:
        emoji = get_emoji(activity, is_return)
        action = f"**{name}** กลับที่นั่งแล้ว" if is_return else f"**{name}** ไป{activity}"
        if timestamp:
            # timestamp format: "dd/mm HH:MM:SS" → extract HH:MM
            time_part = timestamp.split(" ")[-1][:5]
        else:
            time_part = datetime.now().strftime("%H:%M")
        message = f"{emoji} {action}\n> 🕐 {time_part} · 📌 {group_name}"

        guild, member = await self.find_member(discord_user_id)
        if member is None:
            logger.warning(f"No member found for {name} ({discord_user_id})")
            return False, None

        # ห้องทำงาน = ห้องที่อยู่นานสุดในชั่วโมงนี้
        work_ch_id = self.get_work_channel_id(member.id)
        work_vc = await self.find_voice_channel_by_id(str(work_ch_id)) if work_ch_id else None

        # ห้องที่ member อยู่ตอนนี้ (fallback ถ้ายังไม่มีสถิติ)
        current_ch = None
        if member.voice and member.voice.channel:
            ch = member.voice.channel
            if str(ch.id) not in DESTINATION_CHANNEL_IDS:
                current_ch = ch

        if is_return:
            # กลับที่นั่ง → ย้ายกลับถ้า: ถูกบอทย้ายออก หรือ ตอนนี้อยู่ในห้องปลายทาง (กินข้าวเองก่อนกด / บอท restart)
            currently_in_destination = (
                member.voice and
                member.voice.channel and
                str(member.voice.channel.id) in DESTINATION_CHANNEL_IDS
            )
            should_move_back = member.id in self._moved_to_destination or currently_in_destination
            if should_move_back:
                move_target = work_vc or current_ch
                if move_target:
                    try:
                        await member.move_to(move_target)
                        logger.info(f"Moved {name} back → #{move_target.name}")
                    except discord.Forbidden:
                        logger.error(f"No permission to move {name} — bot needs 'Move Members' permission")
                    except Exception as e:
                        logger.error(f"Failed to move {name} back: {e}")
                else:
                    logger.info(f"No work channel found for {name}, skipping return move")
                self._moved_to_destination.discard(member.id)
            else:
                logger.info(f"{name} was not moved out — skipping return move")

            # แจ้งในห้องที่เคยแจ้งกิจกรรมล่าสุด
            last_ch_id = self._last_notified_channel.get(member.id)
            last_vc = await self.find_voice_channel_by_id(str(last_ch_id)) if last_ch_id else None
            notify_vc = last_vc or current_ch

        else:
            target_channel_id = self.get_target_channel_name(activity)

            if target_channel_id:
                # กินข้าว / ทานข้าว → แจ้งในห้องทำงาน แล้วย้ายไป Dining
                notify_vc = work_vc or current_ch
                target_vc = await self.find_voice_channel_by_id(target_channel_id)
                if target_vc:
                    mem_vc = member.voice.channel if member.voice else None
                    if mem_vc and mem_vc.id == target_vc.id:
                        logger.info(f"{name} already in target channel '{target_vc.name}', skipping move")
                    else:
                        try:
                            await member.move_to(target_vc)
                            self._moved_to_destination.add(member.id)
                            logger.info(f"Moved {name} → #{target_vc.name}")
                        except discord.Forbidden:
                            logger.error(f"No permission to move {name} — bot needs 'Move Members' permission")
                        except Exception as e:
                            logger.error(f"Failed to move {name}: {e}")
                else:
                    logger.warning(f"Target channel ID '{target_channel_id}' not found")
            else:
                # ปวดน้อย / ปวดหนัก / พัก → แจ้งห้องที่นั่งอยู่ตอนนี้เลย
                notify_vc = current_ch
                if notify_vc is None:
                    logger.warning(f"{name} is not in any voice channel — notification skipped")

        # ส่งแจ้งเตือน + บันทึกห้องที่แจ้ง (เพื่อใช้ตอนกลับที่นั่ง)
        if notify_vc:
            try:
                if is_return:
                    # reply กลับไปที่ message กิจกรรมล่าสุด (ถ้ามี)
                    ref_msg = self._last_activity_message.get(member.id)
                    if ref_msg and ref_msg.channel.id == notify_vc.id:
                        sent_msg = await ref_msg.reply(message)
                    else:
                        sent_msg = await notify_vc.send(message)
                    self._last_activity_message.pop(member.id, None)
                else:
                    sent_msg = await notify_vc.send(message)
                    self._last_notified_channel[member.id] = notify_vc.id
                    self._last_activity_message[member.id] = sent_msg
                logger.info(f"Sent to voice channel #{notify_vc.name}: {name} - {activity}")
                return True, notify_vc.name
            except Exception as e:
                logger.error(f"Failed to send to voice channel #{notify_vc.name}: {e}")
        elif not is_return:
            logger.warning(f"No work channel found for {name} ({discord_user_id}) — notification skipped")

        return False, None


discord_bot = ActivityBot()


@discord_bot.tree.command(name="status", description="ดูสถานะบอทและห้องทำงานของพนักงาน")
async def status_command(interaction: discord.Interaction):
    # จำกัดให้ใช้ได้เฉพาะ status channel เท่านั้น
    if STATUS_CHANNEL_ID and interaction.channel_id != STATUS_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ คำสั่งนี้ใช้ได้เฉพาะใน status channel เท่านั้น", ephemeral=True
        )
        return

    now = datetime.now().strftime("%d/%m %H:%M:%S")
    hour = datetime.now().hour
    lines = [f"🟢 **Bot Status** — `{now}`",
             f"⏱ Hour slot: `{hour:02d}:00 – {(hour+1)%24:02d}:00`",
             ""]

    # สร้าง reverse map: discord_id → name
    discord_to_name = {v["discord_id"]: v["name"] for v in EMPLOYEES.values()}

    # รวบรวมข้อมูลห้องทำงานของทุกคนที่มีสถิติ
    work_info: list[tuple[str, str, float]] = []  # (name, channel_name, seconds)
    for member_id, ch_map in discord_bot._hour_time.items():
        # รวม ongoing session
        total: dict[int, float] = dict(ch_map)
        if member_id in discord_bot._voice_join_time:
            cur_ch_id, join_ts = discord_bot._voice_join_time[member_id]
            if str(cur_ch_id) not in DESTINATION_CHANNEL_IDS:
                total[cur_ch_id] = total.get(cur_ch_id, 0) + (time_module.time() - join_ts)

        if not total:
            continue

        best_ch_id = max(total, key=lambda c: total[c])
        best_secs = total[best_ch_id]

        # หาชื่อห้องและชื่อพนักงาน
        ch_obj = discord_bot.get_channel(best_ch_id)
        ch_name = f"#{ch_obj.name}" if ch_obj else f"ID:{best_ch_id}"
        disc_id = str(member_id)
        name = discord_to_name.get(disc_id, disc_id)
        work_info.append((name, ch_name, best_secs))

    if work_info:
        work_info.sort(key=lambda x: x[1])  # เรียงตามห้อง
        lines.append(f"**ห้องทำงาน ({len(work_info)} คน):**")
        for name, ch_name, secs in work_info:
            mins = int(secs // 60)
            lines.append(f"• **{name}** → {ch_name} ({mins} นาที)")
    else:
        lines.append("_ยังไม่มีข้อมูลห้องทำงาน (รอพนักงานเข้า voice channel)_")

    await interaction.response.send_message("\n".join(lines))


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
    await discord_bot.wait_until_ready_event()
    await discord_bot.send_status("🟢 **Bot online** — Telegram + Discord connected")

    @client.on(events.NewMessage())
    async def handler(event):
        try:
            chat = await event.get_chat()
            title = getattr(chat, "title", "") or ""

            if not any(g in title for g in TARGET_GROUPS):
                return

            text = event.message.text or ""
            sender = getattr(event.message.sender, "username", None) or getattr(event.message.sender, "first_name", "unknown") if event.message.sender else "unknown"
            logger.info(f"[Jun88] from {sender}: {text[:120]!r}")

            parsed = parse_message(text, title)
            if parsed:
                logger.info(f"[PARSE OK] id={parsed['telegram_id']} activity={parsed['activity']}")
                await on_activity(parsed)
            elif "รหัสผู้ใช้" in text:
                logger.warning(f"[PARSE FAIL] Has รหัสผู้ใช้ but parse failed. Full text: {text!r}")
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}")

    logger.info(f"Listening for groups containing: {TARGET_GROUPS}")
    try:
        await client.run_until_disconnected()
    finally:
        try:
            await client.disconnect()
        except Exception:
            pass


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
        parsed.get("timestamp"),
    )
    status = f"sent to #{channel}" if sent else "not sent (not in voice channel)"
    logger.info(f"{emp['name']} [{parsed['activity']}] → Discord {status}")


async def start_telegram_with_reconnect(on_activity):
    """Wrap start_telegram with auto-reconnect on disconnect."""
    backoff = 5
    first_run = True
    while True:
        try:
            await start_telegram(on_activity)
            # Telegram disconnected cleanly
            await discord_bot.send_status(f"🟡 **Telegram disconnected** — reconnecting in {backoff}s...")
            logger.warning("Telegram disconnected — reconnecting in %ds...", backoff)
        except AuthKeyDuplicatedError:
            # Session ถูกใช้จาก 2 IP พร้อมกัน (Railway overlap) — รอให้ instance เก่าตายก่อน
            wait = 120
            await discord_bot.send_status(
                f"🔴 **Telegram error** — `AuthKeyDuplicatedError` (session conflict)\n"
                f"> รอ {wait}s ให้ instance เก่าตายก่อน..."
            )
            logger.error("AuthKeyDuplicatedError — waiting %ds for old instance to die...", wait)
            await asyncio.sleep(wait)
            backoff = 5  # reset backoff หลังจาก conflict หาย
            await discord_bot.send_status("🔄 **Telegram reconnecting...**")
            continue
        except Exception as e:
            await discord_bot.send_status(f"🔴 **Telegram error** — `{type(e).__name__}` reconnecting in {backoff}s...")
            logger.error("Telegram error: %s — reconnecting in %ds...", e, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)  # cap at 5 minutes
        if not first_run:
            await discord_bot.send_status("🔄 **Telegram reconnecting...**")
        first_run = False


async def start_discord_with_reconnect(token: str):
    """Wrap discord bot start with auto-reconnect on disconnect."""
    backoff = 5
    while True:
        try:
            await discord_bot.start(token)
            logger.warning("Discord disconnected — reconnecting in %ds...", backoff)
        except Exception as e:
            logger.error("Discord error: %s — reconnecting in %ds...", e, backoff)
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


async def main():
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not discord_token:
        logger.warning("Missing DISCORD_BOT_TOKEN. Discord bot disabled.")
        discord_task = asyncio.create_task(asyncio.sleep(0))
    else:
        discord_task = asyncio.create_task(start_discord_with_reconnect(discord_token))

    telegram_task = asyncio.create_task(start_telegram_with_reconnect(on_activity))

    await asyncio.gather(discord_task, telegram_task, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
