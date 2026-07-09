import asyncio
import re
import os
import json
import logging
import time as time_module
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError
import discord
from discord import ChannelType
import aiohttp
from supabase import create_client, Client as SupabaseClient

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
logger = logging.getLogger("bot")

# ---------------------------------------------------------------------------
# Supabase client
# ---------------------------------------------------------------------------
_supabase_url = os.environ.get("SUPABASE_URL", "")
_supabase_key = os.environ.get("SUPABASE_KEY", "")
supabase: SupabaseClient | None = create_client(_supabase_url, _supabase_key) if _supabase_url and _supabase_key else None
if supabase:
    logger.info("[Supabase] Client initialized")
else:
    logger.warning("[Supabase] SUPABASE_URL or SUPABASE_KEY not set — break tracking disabled")
# ---------------------------------------------------------------------------
# EMPLOYEES โหลดจาก Supabase K36 แทน hardcode
# ---------------------------------------------------------------------------
EMPLOYEES: dict[str, dict] = {}  # telegram_id → {name, discord_id, allowed_shift}
_employees_loaded = False

async def load_employees_from_supabase() -> None:
    """โหลดข้อมูลพนักงานจาก Supabase K36 ตาราง users"""
    global EMPLOYEES, _employees_loaded
    if not supabase:
        logger.warning("[EMPLOYEES] Supabase ไม่พร้อม — ใช้ข้อมูลเดิม")
        return
    try:
        res = supabase.from_("users") \
            .select("username, discord_id, telegram_id, allowed_shift, department") \
            .not_.is_("telegram_id", "null") \
            .not_.is_("discord_id", "null") \
            .execute()
        if not res.data:
            logger.warning("[EMPLOYEES] ไม่พบข้อมูลพนักงานใน Supabase")
            return
        new_map = {}
        for u in res.data:
            tid = str(u.get("telegram_id") or "").strip()
            did = str(u.get("discord_id") or "").strip()
            name = str(u.get("username") or "").strip().upper()
            shift = str(u.get("allowed_shift") or "").strip()
            dept = str(u.get("department") or "").strip()
            if tid and did and name:
                new_map[tid] = {
                    "name": name,
                    "discord_id": did,
                    "allowed_shift": shift,
                    "department": dept,
                }
        EMPLOYEES = new_map
        _employees_loaded = True
        logger.info(f"[EMPLOYEES] โหลดสำเร็จ {len(EMPLOYEES)} คน จาก Supabase K36")
    except Exception as e:
        logger.error(f"[EMPLOYEES] load error: {e}")

# TARGET_GROUPS และ SHIFT_GROUPS โหลดจาก Supabase ตาราง checkin_groups
TARGET_GROUPS: list[str] = []   # กลุ่มเช็คอิน (แจ้งพัก)
SHIFT_GROUPS: list[str] = []    # กลุ่มเช็คชื่อ (ถ่ายรูป/กะ)

async def load_target_groups() -> None:
    """โหลดรายชื่อกลุ่ม Telegram จาก Supabase แยกตาม group_type"""
    global TARGET_GROUPS, SHIFT_GROUPS
    if not supabase:
        return
    try:
        res = supabase.from_("checkin_groups")             .select("group_name, group_type")             .eq("active", True)             .execute()
        if res.data:
            checkin = [r["group_name"] for r in res.data if r.get("group_type") == "checkin"]
            shift   = [r["group_name"] for r in res.data if r.get("group_type") == "shift"]
            # TARGET_GROUPS = รวมทั้งหมด (ดักทุกกลุ่ม)
            TARGET_GROUPS = [r["group_name"] for r in res.data]
            SHIFT_GROUPS  = shift
            logger.info(f"[GROUPS] เช็คอิน: {checkin}")
            logger.info(f"[GROUPS] เช็คชื่อ: {shift}")
        else:
            logger.warning("[GROUPS] ไม่พบกลุ่มใน Supabase")
    except Exception as e:
        logger.error(f"[GROUPS] load error: {e}")

# กลุ่มที่ใช้ระบบกะงาน → Sound ID สำหรับแต่ละกลุ่ม
# SHIFT_GROUPS โหลดจาก Supabase แล้ว (ดู load_target_groups)

SHIFT_GROUP_SOUND_ID: dict[str, int] = {
    "OL ชั่วคราว":              1518570639886389378,
    "AM ONLINE เข้างาน":        1518570573943410798,
    "พี่เลี้ยง Jun88 กะ JAPAO": 1518570639886389378,  # ใช้เสียงเดียวกับ OL
}

# จับคู่: กลุ่มกะงาน → กลุ่มเช็คอินกิจกรรมที่ใช้คู่กัน (รองรับหลายกลุ่มได้)
SHIFT_CHECKIN_PAIR: dict[str, list[str]] = {
    "AM ONLINE เข้างาน":        ["Jun88-OL กลุ่มเช็คอิน 打卡群"],
    "OL ชั่วคราว":              ["Jun88-กลุ่มเช็คอิน打卡群"],
    "พี่เลี้ยง Jun88 กะ JAPAO": ["Jun88-OL กลุ่มเช็คอิน 打卡群", "Jun88-กลุ่มเช็คอิน打卡群"],
}

def get_shift_sound_id(group_name: str) -> int:
    return SHIFT_GROUP_SOUND_ID.get(group_name, 0)

# ความยาวเสียง (วินาที) ของแต่ละ sound_id — ใช้รอให้รอบ 1 จบก่อนเปิดรอบ 2
SHIFT_SOUND_DURATION: dict[str, float] = {
    "1518570639886389378": 3.540,  # OL ชั่วคราว
    "1518570573943410798": 2.520,  # AM ONLINE เข้างาน
}

# ข้อความกะงานที่ต้องดักจับ
SHIFT_KEYWORDS = [
    "กะเช้า(08.00-20.00 น.) รอบที่ 1",
    "กะเช้า(08.00-20.00 น.) รอบที่ 2",
    "กะดึก(20.00-08.00 น.) รอบที่ 1",
    "กะดึก(20.00-08.00 น.) รอบที่ 2",
]

def shift_keyword_to_name(keyword: str) -> str:
    for k, v in [("เช้า", "กะเช้า"), ("ดึก", "กะดึก")]:
        if k in keyword:
            for r in ["1", "2"]:
                if f"รอบที่ {r}" in keyword:
                    return f"{v} รอบ {r}"
    return keyword

RETURN_KEYWORDS = ["กลับที่นั่ง", "กลับที่นัง", "回座"]

# ---------------------------------------------------------------------------
# Check-in window & photo tracking
# ---------------------------------------------------------------------------
_checkin_window: dict = {"keyword": None, "shift_name": None, "shift_group": None}
_photos_sent: set[str] = set()        # telegram_id ที่ส่งรูปในช่วงเช็คชื่อนี้แล้ว
_out_during_window: set[str] = set()  # telegram_id ที่ออกไปทำกิจกรรมระหว่าง/ก่อนช่วง
_currently_out: dict[str, str] = {}   # telegram_id → group_name ที่กดเช็คอินมา

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


def get_break_reason(activity: str) -> str:
    """แปลงชื่อกิจกรรมเป็น break_reason ที่ตรงกับ checkin-bot-render"""
    for key, emoji in ACTIVITY_EMOJI.items():
        if key in activity:
            return f"{emoji} {key}"
    return f"☕ {activity}"


def get_thai_time() -> datetime:
    """คืน datetime ปัจจุบันในโซนเวลาไทย (UTC+7)"""
    return datetime.now(timezone(timedelta(hours=7)))


def get_break_date_str() -> str:
    """
    คืนวันที่สำหรับบันทึก break_date
    ถ้าตอนนี้เป็น 00:00-07:59 ไทย (ยังอยู่ในกะดึกที่เริ่มเมื่อวาน) → คืนวันเมื่อวาน
    """
    t = get_thai_time()
    if t.hour < 8:
        t = t - timedelta(days=1)
    return t.strftime("%Y-%m-%d")


def parse_telegram_timestamp(ts_str: str | None) -> datetime | None:
    """แปลง timestamp จาก Telegram เป็น datetime ไทย (UTC+7)
    รองรับทั้ง dd/mm และ mm/dd โดยเลือก format ที่ใกล้เคียงวันปัจจุบันมากสุด"""
    if not ts_str:
        return None
    try:
        parts = ts_str.strip().split()
        date_part, time_part = parts[0], parts[1]
        a, b = map(int, date_part.split('/'))
        h, m, s = map(int, time_part.split(':'))
        year = get_thai_time().year
        today = get_thai_time()

        # ลอง dd/mm (วัน/เดือน)
        try:
            dt_ddmm = datetime(year, b, a, h, m, s, tzinfo=timezone(timedelta(hours=7)))
            diff_ddmm = abs((dt_ddmm - today).total_seconds())
        except ValueError:
            dt_ddmm = None
            diff_ddmm = float("inf")

        # ลอง mm/dd (เดือน/วัน)
        try:
            dt_mmdd = datetime(year, a, b, h, m, s, tzinfo=timezone(timedelta(hours=7)))
            diff_mmdd = abs((dt_mmdd - today).total_seconds())
        except ValueError:
            dt_mmdd = None
            diff_mmdd = float("inf")

        if dt_ddmm is None and dt_mmdd is None:
            return None
        if dt_ddmm is None:
            return dt_mmdd
        if dt_mmdd is None:
            return dt_ddmm

        result = dt_ddmm if diff_ddmm <= diff_mmdd else dt_mmdd
        logger.info(f"[parse_timestamp] {ts_str!r} → {result.strftime('%Y-%m-%d %H:%M:%S')}")
        return result
    except Exception as e:
        logger.warning(f"[parse_telegram_timestamp] parse failed: {ts_str!r} — {e}")
        return None


def get_break_date_from_time(dt: datetime) -> str:
    """คืน break_date จาก datetime — ถ้าก่อน 08:00 ไทย ถือว่าเป็นวันก่อนหน้า (กะดึก)"""
    if dt.hour < 8:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


async def supabase_open_break(staff_name: str, activity: str, timestamp_str: str | None = None) -> None:
    """บันทึกเริ่มพักลง break_sessions"""
    if not supabase:
        return
    try:
        now = parse_telegram_timestamp(timestamp_str) or get_thai_time()
        break_date = get_break_date_from_time(now)
        prev_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        reason = get_break_reason(activity)

        # ถ้ายังมี record ที่ยังไม่ปิดอยู่ → ปิดก่อนแล้วเปิดใหม่ (กรณีบอท Telegram รีเซทข้ามตี 1)
        res = supabase.from_("break_sessions") \
            .select("id, break_start") \
            .eq("staff_name", staff_name) \
            .in_("break_date", [break_date, prev_date]) \
            .is_("break_end", "null") \
            .execute()
        if res.data:
            ids = [r["id"] for r in res.data]
            # ปิด record เก่าด้วยเวลาปัจจุบัน (ถือว่าพักจนถึงตอนนี้)
            supabase.from_("break_sessions") \
                .update({"break_end": now.isoformat()}) \
                .in_("id", ids) \
                .execute()
            logger.info(f"[Supabase] {staff_name} มี record ค้าง {len(ids)} รายการ → ปิดแล้วเปิดใหม่")

        supabase.from_("break_sessions").insert({
            "staff_name": staff_name,
            "break_start": now.isoformat(),
            "break_date": break_date,
            "break_reason": reason,
        }).execute()
        logger.info(f"[Supabase] {staff_name} เริ่มพัก ({reason}) break_date={break_date} เวลา={now.strftime('%H:%M:%S')}")
    except Exception as e:
        logger.error(f"[Supabase] supabase_open_break error: {e}")


async def supabase_close_break(staff_name: str, timestamp_str: str | None = None) -> None:
    """ปิด break_end ให้ record ที่ยังเปิดอยู่ของคนนี้"""
    if not supabase:
        return
    try:
        now = parse_telegram_timestamp(timestamp_str) or get_thai_time()
        break_date = get_break_date_from_time(now)
        prev_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")

        res = supabase.from_("break_sessions") \
            .select("id") \
            .eq("staff_name", staff_name) \
            .in_("break_date", [break_date, prev_date]) \
            .is_("break_end", "null") \
            .execute()
        if not res.data:
            logger.info(f"[Supabase] {staff_name} ไม่มี record พักที่เปิดอยู่")
            return

        ids = [r["id"] for r in res.data]
        supabase.from_("break_sessions") \
            .update({"break_end": now.isoformat()}) \
            .in_("id", ids) \
            .execute()
        logger.info(f"[Supabase] {staff_name} กลับแล้ว ปิด {len(ids)} record เวลา={now.strftime('%H:%M:%S')}")
    except Exception as e:
        logger.error(f"[Supabase] supabase_close_break error: {e}")


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
    timestamp = ts_match.group(1) if ts_match else get_thai_time().strftime("%d/%m %H:%M:%S")

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

    async def play_shift_sound(self, shift_label: str, sound_id: int):
        """เปิดเสียง soundboard ในทุกห้อง voice ที่มีพนักงานอยู่ (2 ครั้งต่อห้อง)"""
        if not sound_id:
            logger.warning(f"[SHIFT] No sound_id for '{shift_label}' — skipping soundboard")
            return

        await self.wait_until_ready_event()

        # รวบรวมห้อง voice ที่มีพนักงานอยู่ (ไม่นับห้องปลายทาง เช่น Dining)
        occupied: dict[int, discord.VoiceChannel] = {}
        for _member_id, (channel_id, _) in self._voice_join_time.items():
            if str(channel_id) not in DESTINATION_CHANNEL_IDS and channel_id not in occupied:
                ch = self.get_channel(channel_id)
                if isinstance(ch, discord.VoiceChannel):
                    occupied[channel_id] = ch

        if not occupied:
            logger.info(f"[SHIFT] {shift_label} — no occupied voice channels, skipping")
            return

        ch_names = [ch.name for ch in occupied.values()]
        logger.info(f"[SHIFT] {shift_label} — playing sound simultaneously in {len(occupied)} channel(s): {ch_names}")

        sound_duration = SHIFT_SOUND_DURATION.get(str(sound_id), 3.0)

        async def play_in_channel(channel: discord.VoiceChannel):
            guild_id = channel.guild.id
            vc: discord.VoiceClient | None = None
            try:
                # Bot ต้องเชื่อมต่ออยู่ใน voice channel ก่อนถึงจะส่ง soundboard effect ได้
                existing_vc = channel.guild.voice_client
                if existing_vc and existing_vc.channel and existing_vc.channel.id == channel.id:
                    vc = existing_vc
                else:
                    if existing_vc:
                        await existing_vc.disconnect(force=True)
                    vc = await channel.connect(timeout=10, reconnect=False)

                # รอบ 1
                await self.http.request(
                    discord.http.Route(
                        "POST",
                        "/channels/{channel_id}/send-soundboard-sound",
                        channel_id=channel.id,
                    ),
                    json={"sound_id": str(sound_id), "source_guild_id": str(guild_id)},
                )
                logger.info(f"[SHIFT] Played sound round 1 in #{channel.name} — waiting {sound_duration}s")
                # รอให้เสียงรอบ 1 จบก่อน
                await asyncio.sleep(sound_duration)
                # รอบ 2
                await self.http.request(
                    discord.http.Route(
                        "POST",
                        "/channels/{channel_id}/send-soundboard-sound",
                        channel_id=channel.id,
                    ),
                    json={"sound_id": str(sound_id), "source_guild_id": str(guild_id)},
                )
                logger.info(f"[SHIFT] Played sound round 2 in #{channel.name}")
            except Exception as e:
                logger.error(f"[SHIFT] Error playing sound in #{channel.name}: {e}")
            finally:
                if vc and vc.is_connected():
                    await vc.disconnect(force=True)

        for ch in occupied.values():
            await play_in_channel(ch)

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
                                 is_return: bool, group_name: str, timestamp: str | None = None,
                                 checkin_reminder: str | None = None) -> tuple[bool, str | None]:
        emoji = get_emoji(activity, is_return)
        action = f"**{name}** กลับที่นั่งแล้ว" if is_return else f"**{name}** ไป{activity}"
        if timestamp:
            # timestamp format: "dd/mm HH:MM:SS" → extract HH:MM then convert +8 → +7 (Thai time)
            raw_time = timestamp.split(" ")[-1][:5]
            try:
                h, m = map(int, raw_time.split(":"))
                h = (h - 1) % 24
                time_part = f"{h:02d}:{m:02d}"
            except Exception:
                time_part = raw_time
        else:
            time_part = datetime.now().strftime("%H:%M")
        message = f"{emoji} {action}\n> 🕐 {time_part} · 📌 {group_name}"
        if checkin_reminder:
            message += f"\n{checkin_reminder}"

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
async def supabase_restore_open_breaks() -> None:
    """ตอน startup โหลด break_sessions ที่ยังเปิดอยู่กลับเข้า _currently_out"""
    if not supabase:
        return
    try:
        break_date = get_break_date_str()
        prev_date = (get_thai_time() - timedelta(days=1)).strftime("%Y-%m-%d")
        res = supabase.from_("break_sessions") \
            .select("staff_name") \
            .in_("break_date", [break_date, prev_date]) \
            .is_("break_end", "null") \
            .execute()
        if not res.data:
            logger.info("[Supabase] ไม่มี open break sessions ค้างอยู่")
            return
        name_to_tid = {v["name"]: k for k, v in EMPLOYEES.items()}
        restored = 0
        for r in res.data:
            tid = name_to_tid.get(r["staff_name"])
            if tid and tid not in _currently_out:
                _currently_out[tid] = ""  # ไม่รู้ group_name เดิม
                restored += 1
        logger.info(f"[Supabase] Restored {restored} open break sessions → _currently_out")
    except Exception as e:
        logger.error(f"[Supabase] supabase_restore_open_breaks error: {e}")


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
    # โหลดกลุ่ม Telegram จาก Supabase K36
    await load_target_groups()
    # โหลดพนักงานจาก Supabase K36
    await load_employees_from_supabase()
    await supabase_restore_open_breaks()
    await discord_bot.send_status("🟢 **Bot online** — Telegram + Discord connected")

    # ดักการเปลี่ยนแปลงตาราง users ผ่าน Supabase Realtime → รีโหลดทันที
    async def watch_employees_realtime():
        try:
            from realtime import AsyncRealtimeClient
            realtime_url = _supabase_url.replace("https://", "wss://") + "/realtime/v1"
            rt_client = AsyncRealtimeClient(realtime_url, _supabase_key)
            await rt_client.connect()
            channel = rt_client.channel("users-changes")
            async def on_users_change(payload):
                logger.info(f"[EMPLOYEES] Realtime: ตรวจพบการเปลี่ยนแปลงใน users → รีโหลดทันที")
                await load_employees_from_supabase()
            await channel.on_postgres_changes(
                event="*",
                schema="public",
                table="users",
                callback=on_users_change
            ).subscribe()
            logger.info("[EMPLOYEES] Realtime: กำลัง watch ตาราง users...")
            await rt_client.listen()
        except ImportError:
            # ถ้าไม่มี realtime library → fallback poll ทุก 60 วินาที
            logger.warning("[EMPLOYEES] ไม่มี realtime library → ใช้ poll ทุก 60 วินาทีแทน")
            while True:
                await asyncio.sleep(60)
                await load_employees_from_supabase()
        except Exception as e:
            logger.error(f"[EMPLOYEES] Realtime error: {e} → fallback poll")
            while True:
                await asyncio.sleep(60)
                await load_employees_from_supabase()
    asyncio.create_task(watch_employees_realtime())

    # ดักการเปลี่ยนแปลงตาราง checkin_groups → รีโหลด TARGET_GROUPS ทันที
    async def watch_groups_realtime():
        try:
            from realtime import AsyncRealtimeClient
            realtime_url = _supabase_url.replace("https://", "wss://") + "/realtime/v1"
            rt_client = AsyncRealtimeClient(realtime_url, _supabase_key)
            await rt_client.connect()
            channel = rt_client.channel("groups-changes")
            async def on_groups_change(payload):
                logger.info(f"[GROUPS] Realtime: ตรวจพบการเปลี่ยนแปลงใน checkin_groups → รีโหลดทันที")
                await load_target_groups()
            await channel.on_postgres_changes(
                event="*",
                schema="public",
                table="checkin_groups",
                callback=on_groups_change
            ).subscribe()
            logger.info("[GROUPS] Realtime: กำลัง watch ตาราง checkin_groups...")
            await rt_client.listen()
        except Exception as e:
            logger.error(f"[GROUPS] Realtime error: {e}")
    asyncio.create_task(watch_groups_realtime())

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

            # กลุ่มกะงาน → ดักข้อความกะเช้า/กะดึก แล้วเปิดเสียง
            if any(g in title for g in SHIFT_GROUPS):
                matched = next((kw for kw in SHIFT_KEYWORDS if kw in text), None)
                if matched:
                    group_key = next((g for g in SHIFT_GROUPS if g in title), "")
                    sound_id = get_shift_sound_id(group_key)
                    logger.info(f"[SHIFT] Detected '{matched}' in '{title}' (sound_id={sound_id})")
                    _checkin_window.update({"keyword": matched, "shift_name": shift_keyword_to_name(matched), "shift_group": group_key})
                    _photos_sent.clear()
                    _out_during_window.clear()
                    paired_checkins = SHIFT_CHECKIN_PAIR.get(group_key, [])
                    _out_during_window.update(
                        tid for tid, grp in _currently_out.items()
                        if not paired_checkins or any(p in grp for p in paired_checkins)
                    )
                    logger.info(f"[CHECKIN] Window opened: {_checkin_window['shift_name']} (paired: {paired_checkins}), {len(_out_during_window)} already out")

                    async def _run_shift_sound(label=matched, sid=sound_id):
                        try:
                            await discord_bot.play_shift_sound(label, sid)
                            logger.info(f"[SHIFT] Done playing sound for '{label}'")
                        except Exception as exc:
                            logger.error(f"[SHIFT] Unhandled error in play_shift_sound: {exc}")

                    asyncio.create_task(_run_shift_sound())
                    return

                if event.message.photo and _checkin_window["keyword"]:
                    sender_id = str(getattr(event.message, "sender_id", None) or "")
                    if sender_id in EMPLOYEES:
                        _photos_sent.add(sender_id)
                        logger.info(f"[CHECKIN] Photo from {EMPLOYEES[sender_id]['name']} recorded")
                return

            # กลุ่มเช็คอินปกติ → parse และแจ้ง Discord
            parsed = parse_message(text, title)
            if parsed:
                logger.info(f"[PARSE OK] id={parsed['telegram_id']} activity={parsed['activity']}")
                await on_activity(parsed)
            elif "รหัสผู้ใช้" in text:
                # กรณีกลับที่นั่งล้มเหลวเพราะบอทเทเลแกรมรีเซท
                is_failed_return = any(kw in text for kw in RETURN_KEYWORDS) and "ไม่มีกิจกรรม" in text
                if is_failed_return:
                    tid_match = re.search(r"รหัสผู้ใช้[：:][^\d]*(\d+)", text)
                    if tid_match:
                        tid = tid_match.group(1)
                        emp = EMPLOYEES.get(tid)
                        if emp:
                            # ปิด break session ใน Supabase เสมอ ไม่ว่าจะอยู่ใน _currently_out หรือไม่
                            # (กรณีบอท Telegram รีเซทข้ามตี 1 ทำให้ _currently_out ว่าง)
                            await supabase_close_break(emp["name"], None)
                            if tid in _currently_out:
                                reminder = None
                                if tid in _out_during_window and tid not in _photos_sent and _checkin_window["keyword"]:
                                    reminder = f"📷 {emp['name']} กลับที่นั่งแล้วอย่าลืมถ่ายรูปเช็คชื่อด้วยนะ! · {_checkin_window['shift_name']}"
                                await discord_bot.send_notification(
                                    emp["discord_id"], emp["name"], "กลับที่นั่ง", True, title,
                                    checkin_reminder=reminder,
                                )
                                _out_during_window.discard(tid)
                                _currently_out.pop(tid, None)
                            logger.info(f"[FAILED RETURN] {emp['name']} — closed break session (bot reset case)")
                else:
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

    tid = parsed["telegram_id"]

    # Track activity state
    if parsed["is_return"]:
        _currently_out.pop(tid, None)
        # ปิด break session ใน Supabase ด้วยเวลาจาก Telegram
        await supabase_close_break(emp["name"], parsed.get("timestamp"))
    else:
        _currently_out[tid] = parsed["group_name"]
        if _checkin_window["keyword"]:
            paired_checkins = SHIFT_CHECKIN_PAIR.get(_checkin_window.get("shift_group", ""), [])
            if not paired_checkins or any(p in parsed["group_name"] for p in paired_checkins):
                _out_during_window.add(tid)
        # เปิด break session ใน Supabase ด้วยเวลาจาก Telegram
        await supabase_open_break(emp["name"], parsed["activity"], parsed.get("timestamp"))

    # เช็คว่าต้องแจ้งเตือนถ่ายรูปไหม
    checkin_reminder = None
    if parsed["is_return"] and _checkin_window["keyword"]:
        if tid in _out_during_window and tid not in _photos_sent:
            checkin_reminder = f"📷 {emp['name']} กลับที่นั่งแล้วอย่าลืมถ่ายรูปเช็คชื่อด้วยนะ! · {_checkin_window['shift_name']}"
        _out_during_window.discard(tid)  # ล้างออกหลังกลับสำเร็จ ป้องกันแจ้งซ้ำ

    await discord_bot.wait_until_ready_event()
    sent, channel = await discord_bot.send_notification(
        emp["discord_id"],
        emp["name"],
        parsed["activity"],
        parsed["is_return"],
        parsed["group_name"],
        parsed.get("timestamp"),
        checkin_reminder=checkin_reminder,
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
