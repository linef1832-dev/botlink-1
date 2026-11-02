import asyncio
import re
import os
import sys
import logging
import time as time_module
from collections import defaultdict
from datetime import datetime, timezone, timedelta

from telethon import TelegramClient, events
from telethon.sessions import StringSession
from telethon.errors import AuthKeyDuplicatedError
import discord
from supabase import create_client, Client as SupabaseClient

from chat_count import ChatCount, spawn, delete_messages_safely, TH_TZ
import nick as nickname

# เวลาใน log เป็นเวลาไทย (เดิมเป็นเวลา server = UTC) และพิมพ์ออก stdout
# (Railway ระบายแดงทุกบรรทัดที่ออก stderr ทำให้ INFO ธรรมดาดูเหมือน error)
logging.Formatter.converter = staticmethod(lambda ts: datetime.fromtimestamp(ts, TH_TZ).timetuple())
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    stream=sys.stdout,
)
logger = logging.getLogger("bot")
# 🔒 ปิด log ระดับ INFO ของไลบรารีที่พิมพ์ API key ของ Supabase ออกมา (realtime พิมพ์ URL ที่มี apikey เต็มๆ)
#    และ httpx ที่พิมพ์ทุก request (นับแชทเช็คทุก 15 วิ ทำให้ log รก) — error/warning ยังแสดงตามปกติ
for _noisy in ("realtime", "realtime._async.client", "httpx", "httpcore"):
    logging.getLogger(_noisy).setLevel(logging.WARNING)

# 🧹 ลบข้อความแจ้งพักใน Discord หลัง "กลับที่นั่ง" กี่วินาที (ลบทั้งข้อความ "ไป..." และ "กลับที่นั่งแล้ว")
#    ค่าเริ่มต้น 7200 = 2 ชั่วโมง (ให้หัวหน้าเลื่อนดูได้ว่าใครกลับมาแล้ว / ใครยังไม่กลับ)
#    ตั้งผ่าน env BREAK_MSG_DELETE_AFTER — 0 = ลบทันที, -1 = ไม่ลบเลย
#    ข้อความ "ไป..." ที่ยังไม่กดกลับ จะไม่ถูกลบ (เห็นตลอดว่าใครยังไม่กลับ)
try:
    BREAK_MSG_DELETE_AFTER = float(os.environ.get("BREAK_MSG_DELETE_AFTER", "7200"))
except ValueError:
    BREAK_MSG_DELETE_AFTER = 7200.0

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


async def sb(fn):
    """[FIX] ไลบรารี supabase เป็นแบบ sync → รันใน thread แยก ไม่ให้บล็อก event loop
    (เดิมเรียกตรงๆ ทำให้ Discord heartbeat / รับข้อความ Telegram / นับแชท ค้างทุกครั้งที่ยิง DB)"""
    return await asyncio.to_thread(fn)

# ---------------------------------------------------------------------------
# EMPLOYEES โหลดจาก Supabase K36 แทน hardcode
# ---------------------------------------------------------------------------
EMPLOYEES: dict[str, dict] = {}  # telegram_id → {name, discord_id, allowed_shift}
# ดัชนีค้นกลับ — สร้างใหม่ทุกครั้งที่โหลดพนักงาน (เดิมสร้าง map ใหม่ซ้ำๆ หลายที่)
NAME_BY_DISCORD: dict[str, str] = {}   # discord_id → ชื่อ
TID_BY_NAME: dict[str, str] = {}       # ชื่อ → telegram_id


def staff_name_by_discord(discord_id) -> str | None:
    return NAME_BY_DISCORD.get(str(discord_id))

async def load_employees_from_supabase() -> None:
    """โหลดข้อมูลพนักงานจาก Supabase K36 ตาราง users"""
    global EMPLOYEES, NAME_BY_DISCORD, TID_BY_NAME
    if not supabase:
        logger.warning("[EMPLOYEES] Supabase ไม่พร้อม — ใช้ข้อมูลเดิม")
        return
    try:
        res = await sb(lambda: supabase.from_("users")
                       .select("username, discord_id, telegram_id, allowed_shift, department")
                       .not_.is_("telegram_id", "null")
                       .not_.is_("discord_id", "null")
                       .execute())
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
        NAME_BY_DISCORD = {v["discord_id"]: v["name"] for v in new_map.values()}
        TID_BY_NAME = {v["name"]: k for k, v in new_map.items()}
        logger.info(f"[EMPLOYEES] โหลดสำเร็จ {len(EMPLOYEES)} คน จาก Supabase K36")
    except Exception as e:
        logger.error(f"[EMPLOYEES] load error: {e}")

# ---------------------------------------------------------------------------
# กลุ่ม Telegram — โหลดทั้งหมดจาก Supabase ตาราง telegram_groups
# ไม่มีชื่อกลุ่ม / sound id / การจับคู่ hardcode ไว้ในไฟล์นี้อีกแล้ว
# แก้ค่าทุกอย่างได้จากหน้าเว็บ แล้วบอทรีโหลดเองผ่าน realtime
# ---------------------------------------------------------------------------
GROUPS_BY_CHAT: dict[str, dict] = {}        # chat_id (normalize แล้ว) -> ข้อมูลกลุ่ม
SHIFT_CHAT_IDS: set[str] = set()            # chat_id ของกลุ่มประเภท shift (ใช้เพื่อ "ข้าม" ไม่ประมวลผล)
_seen_unknown_chats: set[str] = set()       # กัน log ซ้ำสำหรับกลุ่มที่ยังไม่ได้ตั้งค่า

# ปิดเสียง log ของกลุ่มที่ไม่ได้ตั้งค่าไว้ (ค่าเริ่มต้น = ปิด เพื่อไม่ให้ log รก)
# ตอนจะหา chat_id ของกลุ่มใหม่ ให้ตั้ง env LOG_UNKNOWN_GROUPS=1 แล้วรีสตาร์ทบอท
LOG_UNKNOWN_GROUPS = os.environ.get("LOG_UNKNOWN_GROUPS", "0").strip().lower() in ("1", "true", "yes", "on")


def norm_chat_id(value) -> str:
    """ทำให้ chat id รูปแบบต่างๆ เทียบกันได้

    Telethon คืน 1234567890 แต่ Bot API / ลิงก์ คืน -1001234567890
    ทั้งคู่คือกลุ่มเดียวกัน ฟังก์ชันนี้ตัดเครื่องหมายลบและ prefix 100 ออกให้เหลือแกนเดียวกัน
    จะได้ไม่ต้องมานั่งเดาว่าต้องกรอกแบบไหนในหน้าเว็บ
    """
    s = str(value or "").strip()
    if not s:
        return ""
    s = s.lstrip("-")
    if not s.isdigit():
        return ""
    if s.startswith("100") and len(s) > 10:
        s = s[3:]
    return s


async def load_target_groups() -> None:
    """โหลดกลุ่ม Telegram จาก Supabase ตาราง telegram_groups"""
    global GROUPS_BY_CHAT, SHIFT_CHAT_IDS
    if not supabase:
        return
    try:
        res = await sb(lambda: supabase.from_("telegram_groups")
                       .select("group_name, group_type, chat_id")
                       .eq("active", True)
                       .execute())

        groups: dict[str, dict] = {}
        shifts: set[str] = set()
        missing_id: list[str] = []

        for r in (res.data or []):
            cid = norm_chat_id(r.get("chat_id"))
            if not cid:
                missing_id.append(r.get("group_name") or "(ไม่มีชื่อ)")
                continue

            groups[cid] = {
                "name": r.get("group_name") or "",
                "type": r.get("group_type") or "checkin",
            }
            if groups[cid]["type"] == "shift":
                shifts.add(cid)

        GROUPS_BY_CHAT = groups
        SHIFT_CHAT_IDS = shifts
        _seen_unknown_chats.clear()

        logger.info(f"[GROUPS] โหลดแล้ว {len(groups)} กลุ่ม (กะ {len(shifts)} กลุ่ม)")
        for cid, g in groups.items():
            logger.info(f"[GROUPS]   chat_id={cid} · {g['name']} · {g['type']}")
        if missing_id:
            logger.warning(f"[GROUPS] ข้าม {len(missing_id)} กลุ่ม เพราะยังไม่ได้ใส่ Chat ID: {missing_id}")
        if not groups:
            logger.warning("[GROUPS] ไม่มีกลุ่มที่ใช้งานได้เลย — บอทจะไม่ดักข้อความจากที่ไหน")
    except Exception as e:
        logger.error(f"[GROUPS] load error: {e}")


async def log_unknown_chat(event, chat_id: str) -> None:
    """พิมพ์ chat_id ของกลุ่มที่ยังไม่ได้ตั้งค่า ครั้งเดียวต่อกลุ่ม

    เอาไว้ให้แอดมินเปิด log แล้วก๊อป chat_id ไปใส่ในหน้าเว็บได้เลย
    ดึงชื่อกลุ่มเฉพาะตอนเจอกลุ่มใหม่ครั้งแรกเท่านั้น ไม่ใช่ทุกข้อความ
    """
    if not LOG_UNKNOWN_GROUPS:
        return
    if not chat_id or chat_id in _seen_unknown_chats:
        return
    _seen_unknown_chats.add(chat_id)
    title = ""
    try:
        chat = await event.get_chat()
        title = getattr(chat, "title", "") or ""
    except Exception:
        pass
    logger.info(f"[GROUPS] พบกลุ่มที่ยังไม่ได้ตั้งค่า → chat_id={chat_id} · ชื่อ: {title!r}")


RETURN_KEYWORDS = ["กลับที่นั่ง", "กลับที่นัง", "回座"]
# รหัสผู้ใช้ — รองรับทั้ง "รหัสผู้ใช้：12345" และ "**รหัสผู้ใช้：** `12345`"
TID_RE = re.compile(r"รหัสผู้ใช้[：:][^\d]*(\d+)")

_currently_out: dict[str, str] = {}   # telegram_id → chat_id ของกลุ่มที่กดเช็คอินมา

# อีโมจิที่ "บันทึกลง K36" (break_reason) — ต้องตรงกับ checkin-bot-render ห้ามเปลี่ยน
BREAK_REASON_EMOJI = {
    "กินข้าว": "🍚", "ทานข้าว": "🍚",
    "ปวดหนัก": "🚽", "ห้องน้ำใหญ่": "🚽",
    "ปวดน้อย": "🚾", "ห้องน้ำเล็ก": "🚾",
}
# อีโมจิที่ "แสดงใน Discord" (ข้อความแจ้งพัก + หน้าชื่อ) — เปลี่ยนได้อิสระ
ACTIVITY_EMOJI = {
    "กินข้าว": "🍚", "ทานข้าว": "🍚",
    "ปวดหนัก": "🚽", "ห้องน้ำใหญ่": "🚽",
    "ปวดน้อย": "🚬", "ห้องน้ำเล็ก": "🚬",
}

# ---------------------------------------------------------------------------
# Activity → Discord voice channel auto-move
# Map activity keyword → Discord voice channel ID (as string)
# Leave empty string to disable auto-move for that activity
# ---------------------------------------------------------------------------
ACTIVITY_MOVE_CHANNEL: dict[str, str] = {
    "กินข้าว": "1451121950297686106",
    "ทานข้าว": "1451121950297686106",
    "ปวดหนัก": "",
    "ปวดน้อย": "",
}

# ห้องที่ไม่นับเป็นห้องทำงาน (ห้องปลายทาง)
DESTINATION_CHANNEL_IDS: set[str] = {
    v for v in ACTIVITY_MOVE_CHANNEL.values() if v
}


def is_destination(channel_id) -> bool:
    """ห้องปลายทาง (ห้องกินข้าว) — ไม่นับเป็นห้องทำงาน"""
    return str(channel_id) in DESTINATION_CHANNEL_IDS


# ── 🏷️ ชื่อเล่นพนักงาน = [สีเว็บวันนี้][ชื่อเดิม] [อีโมจิพัก] ── เช่น "🟣👑 LAYLA 🚽" (รายละเอียดใน nick.py)
# บอทต้องมีสิทธิ์ Manage Nicknames และ role สูงกว่าพนักงาน
# NICK_STATUS=0 → ปิดอีโมจิพัก / WEB_NICK=0 → ปิดสีเว็บ
NICK_STATUS_ENABLED = os.environ.get("NICK_STATUS", "1").strip() != "0"
WEB_NICK_ENABLED = os.environ.get("WEB_NICK", "1").strip() != "0"
WEB_NICK_SYNC_EVERY = 300         # อ่านตารางหน้าที่จาก K36 ใหม่ทุกกี่วินาที (แอดมินแก้หน้าที่ → ชื่อเปลี่ยนตามภายใน 5 นาที)
NICK_RETRY_AFTER = 300            # เปลี่ยนชื่อไม่ได้ → รอกี่วินาทีค่อยลองใหม่
# กิจกรรมที่ไม่ต้องใส่อีโมจิพัก — กินข้าวถูกย้ายไปห้องกินข้าวอยู่แล้ว เห็นจากห้องได้เลย
NICK_SKIP_EMOJIS = {"🍚"}
# อีโมจิพักทั้งหมดที่บอทอาจใส่ (รวมรุ่นเก่า เช่น 🚾 / 🍚 ไว้ด้วย เพื่อให้ยังลบออกจากชื่อที่ค้างอยู่ได้)
BREAK_NICK_EMOJIS = set(ACTIVITY_EMOJI.values()) | set(BREAK_REASON_EMOJI.values()) | {"🚶"}

WEB_EMOJI: dict[str, str] = nickname.web_emoji_map([])   # ชื่อเว็บ(ตัวเล็ก) → สี
# discord_id → [เว็บงานหลัก, (เว็บงานรอง — OD)] ([] = วันนี้ไม่ได้ทำเว็บไหน / ไม่มี key = ยังไม่รู้ ไม่แตะ)
TODAY_WEB: dict[str, list[str]] = {}


async def load_today_webs() -> None:
    """อ่านสีเว็บ + ตารางหน้าที่ของวันนี้จาก K36 แล้วคำนวณว่าใครทำเว็บไหน"""
    global WEB_EMOJI, TODAY_WEB
    if not supabase or not EMPLOYEES:
        return
    try:
        res = await sb(lambda: supabase.from_("chatcount_settings").select("webs").eq("id", 1).execute())
        WEB_EMOJI = nickname.web_emoji_map(((res.data or [{}])[0] or {}).get("webs"))
    except Exception as e:
        logger.warning(f"[WEB_NICK] โหลดสีเว็บไม่ได้ ใช้ค่าเดิม: {e}")
    now = get_thai_time()
    keys = sorted({k for emp in EMPLOYEES.values() for k in nickname.roster_keys(emp, now)})
    try:
        res = await sb(lambda: supabase.from_("settings").select("key, value").in_("key", keys).execute())
    except Exception as e:
        logger.warning(f"[WEB_NICK] โหลดตารางหน้าที่ไม่ได้: {e}")
        return
    rows = {r["key"]: r.get("value") for r in (res.data or [])}
    TODAY_WEB = nickname.resolve_today_webs(EMPLOYEES, rows, now)


def web_emoji_for(team: str) -> str:
    return WEB_EMOJI.get((team or "").strip().lower(), "")


def web_prefix_for(teams: list[str]) -> str:
    """สีหน้าชื่อ เช่น ["PG688", "F168"] → "🟣🟠" (สีซ้ำ/ไม่มีสี ตัดทิ้ง)"""
    out = []
    for t in teams or []:
        e = web_emoji_for(t)
        if e and e not in out:
            out.append(e)
    return "".join(out)


def get_emoji(activity: str, is_return: bool) -> str:
    if is_return:
        return "✅"
    for key, emoji in ACTIVITY_EMOJI.items():
        if key in activity:
            return emoji
    return "🚶"


def get_break_reason(activity: str) -> str:
    """แปลงชื่อกิจกรรมเป็น break_reason ที่ตรงกับ checkin-bot-render"""
    for key, emoji in BREAK_REASON_EMOJI.items():
        if key in activity:
            return f"{emoji} {key}"
    return f"☕ {activity}"


# โซนเวลา
#   TG_TZ = โซนของเวลาที่บอทเช็คอินฝั่ง Telegram พิมพ์มาในข้อความ = เวลาไทย (UTC+7)
#           (ตรวจกับข้อความจริงแล้ว: ข้อความส่ง 23:11 ไทย เวลาในข้อความก็ 23:11 → ไม่ต้องแปลงโซน)
#   TH_TZ = โซนไทย (UTC+7) ที่ใช้เก็บลง DB และแสดงใน Discord
# ถ้าวันหนึ่งบอทต้นทางเปลี่ยนไปใช้โซนอื่น ตั้ง env TELEGRAM_TZ_OFFSET ได้เลย (เช่น 8 = เวลาจีน) ไม่ต้องแก้โค้ด
# (TH_TZ import มาจาก chat_count.py — ประกาศไว้ที่เดียว)
try:
    _TG_OFFSET_HOURS = float(os.environ.get("TELEGRAM_TZ_OFFSET", "7"))
except ValueError:
    _TG_OFFSET_HOURS = 7.0
TG_TZ = timezone(timedelta(hours=_TG_OFFSET_HOURS))


def get_thai_time() -> datetime:
    """คืน datetime ปัจจุบันในโซนเวลาไทย (UTC+7)"""
    return datetime.now(TH_TZ)


def get_break_date_from_time(dt: datetime) -> str:
    """คืน break_date จาก datetime — ถ้าก่อน 08:00 ไทย ถือว่าเป็นวันก่อนหน้า (กะดึกที่เริ่มเมื่อวาน)"""
    if dt.hour < 8:
        dt = dt - timedelta(days=1)
    return dt.strftime("%Y-%m-%d")


def parse_telegram_timestamp(ts_str: str | None) -> datetime | None:
    """แปลง timestamp จากข้อความ Telegram ("dd/mm HH:MM:SS" ในโซน TG_TZ) เป็น datetime ไทย (UTC+7)

    ข้อความไม่มีปีมาด้วย และบางทีเป็น dd/mm บางทีเป็น mm/dd
    → ลองทุกแบบ (ปีที่แล้ว/ปีนี้/ปีหน้า × dd/mm/mm/dd) แล้วเลือกอันที่ใกล้เวลาปัจจุบันที่สุด
    [FIX] เดิมใช้ปีปัจจุบันอย่างเดียว ข้อความ 31/12 ที่ประมวลผลหลังเที่ยงคืนปีใหม่จะกลายเป็นปลายปีหน้า"""
    if not ts_str:
        return None
    try:
        parts = ts_str.strip().split()
        date_part, time_part = parts[0], parts[1]
        a, b = map(int, date_part.split('/'))
        h, m, s = map(int, time_part.split(':'))
        today = datetime.now(TG_TZ)                      # เทียบในโซนเดียวกับข้อความ

        best: datetime | None = None
        best_diff = float("inf")
        for year in (today.year - 1, today.year, today.year + 1):
            for month, day in ((b, a), (a, b)):          # dd/mm ก่อน (ถ้าใกล้เท่ากันใช้ dd/mm)
                try:
                    dt = datetime(year, month, day, h, m, s, tzinfo=TG_TZ)
                except ValueError:
                    continue
                diff = abs((dt - today).total_seconds())
                if diff < best_diff:
                    best, best_diff = dt, diff

        if best is None:
            return None
        best = best.astimezone(TH_TZ)                    # แปลงเป็นเวลาไทย (ถ้า TG_TZ = +7 อยู่แล้ว ค่าไม่เปลี่ยน)
        logger.info(f"[parse_timestamp] {ts_str!r} (UTC{_TG_OFFSET_HOURS:+g}) → {best.strftime('%Y-%m-%d %H:%M:%S')} ไทย")
        return best
    except Exception as e:
        logger.warning(f"[parse_telegram_timestamp] parse failed: {ts_str!r} — {e}")
        return None


# ตารางที่บันทึกการพัก
#   break_logs     = ตารางหลัก บันทึกโดย botlink คนเดียว → K36 ดึงไปแสดง
#   break_sessions = ตารางเดิม เขียนสำเนาไว้ให้ checkin-bot-render ใช้แจ้งเตือนพักนานต่อได้
BREAK_TABLES = ["break_logs", "break_sessions"]


# ล็อก "ต่อคน" — งานของคนเดียวกัน (บันทึก DB + แจ้ง/ย้ายห้อง Discord) ทำทีละอย่างตามลำดับที่กด
# เช่น กด "ไปกินข้าว" แล้ว "กลับที่นั่ง" ติดๆ กัน → ต้องย้ายไปห้องกินข้าวให้เสร็จก่อน แล้วค่อยย้ายกลับ
# (คนละคนทำพร้อมกันได้ ไม่ต้องรอคิวกัน — ตอนเปลี่ยนกะที่กดพร้อมกันหลายคน จะไม่ช้า)
_person_locks: dict[str, asyncio.Lock] = {}


def person_lock(key: str) -> asyncio.Lock:
    lock = _person_locks.get(key)
    if lock is None:
        lock = _person_locks[key] = asyncio.Lock()
    return lock


def _find_open_break_ids(table: str, staff_name: str, now: datetime) -> list:
    """id ของ record พักที่ยังไม่ปิดของคนนี้ (วันนี้ + เมื่อวาน)"""
    break_date = get_break_date_from_time(now)
    prev_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
    res = supabase.from_(table) \
        .select("id") \
        .eq("staff_name", staff_name) \
        .in_("break_date", [break_date, prev_date]) \
        .is_("break_end", "null") \
        .execute()
    return [r["id"] for r in (res.data or [])]


def _close_break_ids(table: str, ids: list, now: datetime) -> None:
    supabase.from_(table) \
        .update({"break_end": now.isoformat()}) \
        .in_("id", ids) \
        .execute()


def _open_break_in(table: str, staff_name: str, reason: str, now: datetime) -> None:
    break_date = get_break_date_from_time(now)

    # ถ้ายังมี record ที่ยังไม่ปิดอยู่ → ปิดก่อนแล้วเปิดใหม่ (กรณีบอท Telegram รีเซทข้ามตี 1)
    ids = _find_open_break_ids(table, staff_name, now)
    if ids:
        _close_break_ids(table, ids, now)
        logger.info(f"[{table}] {staff_name} มี record ค้าง {len(ids)} รายการ → ปิดแล้วเปิดใหม่")

    supabase.from_(table).insert({
        "staff_name": staff_name,
        "break_start": now.isoformat(),
        "break_date": break_date,
        "break_reason": reason,
    }).execute()
    logger.info(f"[{table}] {staff_name} เริ่มพัก ({reason}) break_date={break_date} เวลา={now.strftime('%H:%M:%S')}")


def _close_break_in(table: str, staff_name: str, now: datetime) -> None:
    ids = _find_open_break_ids(table, staff_name, now)
    if not ids:
        logger.info(f"[{table}] {staff_name} ไม่มี record พักที่เปิดอยู่")
        return
    _close_break_ids(table, ids, now)
    logger.info(f"[{table}] {staff_name} กลับแล้ว ปิด {len(ids)} record เวลา={now.strftime('%H:%M:%S')}")


async def supabase_open_break(staff_name: str, activity: str, timestamp_str: str | None = None) -> None:
    """บันทึกเริ่มพักลงทุกตารางใน BREAK_TABLES (ตารางหนึ่งพัง อีกตารางยังบันทึกได้)"""
    if not supabase:
        return
    now = parse_telegram_timestamp(timestamp_str) or get_thai_time()
    reason = get_break_reason(activity)
    for table in BREAK_TABLES:
        try:
            await sb(lambda t=table: _open_break_in(t, staff_name, reason, now))
        except Exception as e:
            logger.error(f"[{table}] supabase_open_break error: {e}")


async def supabase_close_break(staff_name: str, timestamp_str: str | None = None) -> None:
    """ปิด break_end ให้ record ที่ยังเปิดอยู่ของคนนี้ ในทุกตารางใน BREAK_TABLES"""
    if not supabase:
        return
    now = parse_telegram_timestamp(timestamp_str) or get_thai_time()
    for table in BREAK_TABLES:
        try:
            await sb(lambda t=table: _close_break_in(t, staff_name, now))
        except Exception as e:
            logger.error(f"[{table}] supabase_close_break error: {e}")


def parse_message(text: str, group_name: str, chat_id: str = "") -> dict | None:
    if not text:
        return None

    # Skip error/failed messages (❌ with ไม่สามารถ)
    if "❌" in text and ("ไม่สามารถ" in text or "失败" in text):
        return None

    # Extract Telegram ID — supports both plain and markdown-wrapped:
    # "รหัสผู้ใช้：12345"  or  "**รหัสผู้ใช้：** `12345`"
    tid_match = TID_RE.search(text)
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
    # ไม่มีเวลาในข้อความ → ใช้เวลาปัจจุบัน "ในโซนเดียวกับข้อความ Telegram" (จะถูกแปลงเป็นไทยทีหลังเหมือนกัน)
    timestamp = ts_match.group(1) if ts_match else datetime.now(TG_TZ).strftime("%d/%m %H:%M:%S")

    dur_match = re.search(r"เวลากิจกรรมนี้[：:][^`]*`([^`]+)`", text)
    duration = dur_match.group(1) if dur_match else None

    return {
        "telegram_id": tid_match.group(1),
        "activity": "กลับที่นั่ง" if is_return else activity,
        "is_return": is_return,
        "timestamp": timestamp,
        "duration": duration,
        "group_name": group_name,
        "chat_id": chat_id,
    }


# ข้อความแจ้งพักของบอทใน Discord:  "{emoji} **ชื่อ** ไป...\n> 🕐 HH:MM"
#                                   "✅ **ชื่อ** กลับที่นั่งแล้ว\n> 🕐 HH:MM"
# (ข้อความรุ่นเก่ามี " · 📌 ชื่อกลุ่ม" ต่อท้าย — ยังจับได้ เพื่อให้ลบข้อความเก่าที่ค้างอยู่ได้ด้วย)
# ต้องตรงทั้งข้อความ ไม่ใช่แค่บางส่วน — กันไปจับข้อความอื่นของบอท (สถานะ / นับแชท)
_BREAK_MSG_RE = re.compile(
    r"[^\s*]+ \*\*(.+?)\*\* (กลับที่นั่งแล้ว|ไป)[^\n]*\n> 🕐 \d{1,2}:\d{2}(?: · 📌 [^\n]*)?"
)


def parse_break_message(m, bot_user_id) -> tuple[str, str] | None:
    """ถ้าเป็นข้อความแจ้งพักของบอท คืน (ชื่อ, "กลับที่นั่งแล้ว" หรือ "ไป") ไม่ใช่คืน None
    ใช้ร่วมกันทั้งตัวเก็บกวาดอัตโนมัติ และคำสั่ง /clear_break_messages"""
    if bot_user_id is None or m.author.id != bot_user_id:
        return None
    mt = _BREAK_MSG_RE.fullmatch((m.content or "").strip())   # ไม่ตรงรูปแบบ = ไม่ใช่ข้อความแจ้งพัก
    return (mt.group(1), mt.group(2)) if mt else None


# ---------------------------------------------------------------------------
# Discord bot
# ---------------------------------------------------------------------------
_out_restored = False     # โหลดรายชื่อคนที่ยังพักอยู่จาก K36 เสร็จแล้วหรือยัง (ก่อนนั้นห้ามคืนชื่อ)
STATUS_CHANNEL_ID = int(os.environ.get("DISCORD_STATUS_CHANNEL_ID", "0") or "0")


class ActivityBot(discord.Client):
    def __init__(self):
        intents = discord.Intents.default()
        intents.members = True
        intents.voice_states = True
        # 📊 นับแชท ต้องอ่านข้อความรีพลาย → ต้องเปิด MESSAGE CONTENT INTENT ใน Discord Developer Portal ด้วย
        intents.message_content = True
        super().__init__(intents=intents)
        self._ready_event = asyncio.Event()
        self.tree = discord.app_commands.CommandTree(self)

        # ระบบจำห้องทำงานแบบนับเวลา รายชั่วโมง
        # member_id → (channel_id, join_timestamp) — ห้องที่กำลังอยู่ตอนนี้
        self._voice_join_time: dict[int, tuple[int, float]] = {}
        # member_id → {channel_id: วินาทีที่อยู่} — สะสมไว้ตลอดชั่วโมงนี้
        self._hour_time: dict[int, dict[int, float]] = defaultdict(lambda: defaultdict(float))
        # ชั่วโมงปัจจุบัน (0-23) เพื่อรู้เมื่อขึ้นชั่วโมงใหม่
        self._current_hour: int = get_thai_time().hour
        # member_id → channel_id ห้องที่แจ้งกิจกรรมล่าสุด (ใช้สำหรับ "กลับที่นั่ง")
        self._last_notified_channel: dict[int, int] = {}
        # member_id → discord.Message ของกิจกรรมล่าสุด (ใช้สำหรับ reply ตอนกลับที่นั่ง)
        self._last_activity_message: dict[int, discord.Message] = {}
        # ── ห้องที่ต้องย้ายกลับ ──
        # [FIX] เดิมใช้ "ห้องที่อยู่นานสุดในชั่วโมงนี้" อย่างเดียว ซึ่งถูกล้างทุกต้นชั่วโมง
        #       → กินข้าวข้ามชั่วโมง (เช่น ไป 12:50 กลับ 13:10) บอทหาห้องเดิมไม่เจอ ไม่ย้ายกลับ
        #       → และถ้าชั่วโมงนั้นย้ายห้องมา ก็ย้ายกลับผิดห้อง
        # member_id → ห้องที่อยู่ "ตอนบอทย้ายไปห้องกินข้าว" (ห้องเดิมจริงๆ)
        self._return_channel: dict[int, int] = {}
        # member_id → ห้องทำงานล่าสุดที่อยู่ (ไม่นับห้องกินข้าว) — ไม่ถูกล้างรายชั่วโมง
        self._last_work_channel: dict[int, int] = {}
        # ── ชื่อเล่น ── member_id → อีโมจิพักที่ต้องแสดง (None = ไม่ได้พัก / กลับแล้ว)
        self._break_emoji: dict[int, str | None] = {}
        # เปลี่ยนชื่อไม่ได้ (role สูงกว่าบอท) → member_id: เวลาที่ลองล่าสุด
        # พักไว้ 5 นาทีค่อยลองใหม่ (ถ้าแอดมินแก้ลำดับ role แล้ว จะใช้ได้เองโดยไม่ต้องรีสตาร์ทบอท)
        self._nick_forbidden: dict[int, float] = {}
        # channel_id → เวลาที่แจ้งกลับที่นั่งล่าสุด (ตัวเก็บกวาดตรวจเฉพาะห้องเหล่านี้)
        self._sweep_channels: dict[int, float] = {}

    async def setup_hook(self):
        self._commands_synced = False

    async def _sync_commands(self):
        """ลงทะเบียน slash command แบบ "ต่อเซิร์ฟเวอร์" → ขึ้นในเมนู / ทันที (แบบ global อาจรอนาน)
        แล้วล้างชุด global เก่าทิ้ง กันคำสั่งขึ้นซ้ำ 2 อัน"""
        if self._commands_synced:
            return
        self._commands_synced = True
        try:
            for g in self.guilds:
                self.tree.copy_global_to(guild=g)
                cmds = await self.tree.sync(guild=g)
                logger.info(f"[COMMANDS] ลงทะเบียน {len(cmds)} คำสั่งในเซิร์ฟเวอร์ '{g.name}': {', '.join('/' + c.name for c in cmds)}")
            self.tree.clear_commands(guild=None)
            await self.tree.sync()
        except discord.Forbidden:
            logger.error("[COMMANDS] ลงทะเบียนคำสั่งไม่ได้ — ต้องเชิญบอทใหม่ด้วย scope 'applications.commands'")
        except Exception as e:
            logger.error(f"[COMMANDS] ลงทะเบียนคำสั่งไม่สำเร็จ: {e}")

    async def on_ready(self):
        logger.info(f"Discord bot ready: {self.user}")
        now = time_module.time()
        count = 0
        for guild in self.guilds:
            for channel in guild.voice_channels:
                for member in channel.members:
                    if not is_destination(channel.id):
                        # on_ready เกิดซ้ำได้ตอน Discord reconnect → ถ้ายังอยู่ห้องเดิม ไม่ต้องรีเซ็ตเวลาที่นับไว้
                        prev = self._voice_join_time.get(member.id)
                        if prev is None or prev[0] != channel.id:
                            self._voice_join_time[member.id] = (channel.id, now)
                        self._last_work_channel[member.id] = channel.id
                        count += 1
        if count:
            logger.info(f"Snapshot: found {count} member(s) already in voice channels")
        self._ready_event.set()          # ⚠️ ต้องถึงบรรทัดนี้เสมอ — งาน Telegram/นับแชท รอสัญญาณนี้อยู่
        await self._sync_commands()
        if (WEB_NICK_ENABLED or NICK_STATUS_ENABLED) and not getattr(self, "_nick_sync_started", False):
            self._nick_sync_started = True
            spawn(self._nick_sync_loop())
        if BREAK_MSG_DELETE_AFTER >= 0 and not getattr(self, "_sweeper_started", False):
            self._sweeper_started = True
            spawn(self._break_sweeper())

    SWEEP_EVERY = 120          # วินาที
    # ห้องที่มีคนกลับที่นั่ง จะถูกตรวจต่อไปจนเลยเวลาลบ + 30 นาที (ต้องนานกว่าเวลาลบ ไม่งั้นตามลบไม่ทัน)
    SWEEP_KEEP = max(BREAK_MSG_DELETE_AFTER, 0) + 30 * 60
    SWEEP_HISTORY = 200        # ย้อนดูกี่ข้อความต่อห้อง (รอ 2 ชม. ห้องที่คุยเยอะข้อความจะดันขึ้นไปไกล)

    async def _break_sweeper(self):
        """🧹 เก็บกวาดข้อความ "กลับที่นั่งแล้ว" ที่ค้าง ทุก 2 นาที
        การลบหลังหน่วงเวลา (ค่าเริ่มต้น 2 ชม.) เป็นงานในความจำ ถ้าบอทรีสตาร์ท/deploy ระหว่างรอ งานลบจะหาย → ตัวนี้ตามลบให้
        (ลบทั้งข้อความกลับที่นั่ง + ข้อความ "ไป..." ที่มันรีพลายอยู่)

        [FIX] เดิมอ่านประวัติทุกห้องทุก 2 นาที เปลือง API มาก
        ตอนนี้: รอบแรกหลังเปิดบอทตรวจทุกห้อง (เก็บของที่ค้างจากก่อนรีสตาร์ท)
                รอบต่อไปตรวจเฉพาะห้องที่ยังมีข้อความกลับที่นั่งรอลบอยู่"""
        await asyncio.sleep(30)
        full = True
        while True:
            try:
                removed = await self._sweep_once(full)
                full = False
                if removed:
                    logger.info(f"[SWEEP] ลบข้อความกลับที่นั่งที่ค้าง {removed} ชุด")
            except Exception as e:
                logger.warning(f"[SWEEP] error: {e}")
            await asyncio.sleep(self.SWEEP_EVERY)

    def desired_nick(self, member: discord.Member) -> str | None:
        """ชื่อเล่นที่ควรเป็นตอนนี้ (None = ไม่ตั้งชื่อเล่น ใช้ชื่อบัญชี)"""
        did = str(member.id)
        web_cur, base, brk_cur = nickname.parse_nick(member.nick, WEB_EMOJI.values(), BREAK_NICK_EMOJIS)
        base_text = base or member.global_name or member.name

        web = web_cur                                   # ไม่รู้ตารางวันนี้ → คงสีเดิม
        if WEB_NICK_ENABLED and did in TODAY_WEB:
            web = web_prefix_for(TODAY_WEB[did])

        if not NICK_STATUS_ENABLED:
            brk = ""
        elif member.id in self._break_emoji:
            brk = self._break_emoji[member.id] or ""
        elif not _out_restored:
            brk = brk_cur                               # ยังไม่รู้ว่าใครพักอยู่ (เพิ่งเปิดบอท) → คงไว้ก่อน
        else:
            out_ids = {EMPLOYEES[t]["discord_id"] for t in _currently_out if t in EMPLOYEES}
            brk = brk_cur if did in out_ids else ""     # บอทรีสตาร์ทระหว่างพัก → คงอีโมจิเดิมจนกว่าจะกดกลับ
        if brk in NICK_SKIP_EMOJIS:
            brk = ""

        if not web and not brk and (not base or base in (member.global_name, member.name)):
            return None                                 # ไม่มีอะไรต้องเติม และชื่อเท่าชื่อบัญชี → ไม่ต้องตั้งชื่อเล่น
        return nickname.build_nick(base_text, web, brk)

    async def apply_nick(self, member: discord.Member) -> bool:
        """เปลี่ยนชื่อเล่นให้ตรงกับที่ควรเป็น คืน True ถ้ามีการเปลี่ยนจริง"""
        if member.id == getattr(member.guild, "owner_id", None):
            return False                                # Discord ไม่ให้บอทเปลี่ยนชื่อเจ้าของเซิร์ฟเวอร์
        last_fail = self._nick_forbidden.get(member.id)
        if last_fail is not None and time_module.time() - last_fail < NICK_RETRY_AFTER:
            return False
        new = self.desired_nick(member)
        if (new or None) == (member.nick or None):
            return False
        try:
            await member.edit(nick=new, reason="สีเว็บ/สถานะพัก (botlink)")
        except discord.Forbidden:
            self._nick_forbidden[member.id] = time_module.time()
            top = getattr(getattr(member, "top_role", None), "name", "?")
            logger.warning(f"[NICK] เปลี่ยนชื่อ {member} ไม่ได้ — role สูงสุดของคนนี้คือ '{top}' "
                           f"ต้องลาก role ของบอทให้อยู่เหนือ role นี้ (และเปิดสิทธิ์ Manage Nicknames)")
            return False
        except discord.HTTPException as e:
            logger.warning(f"[NICK] เปลี่ยนชื่อ {member} ไม่สำเร็จ: {e}")
            return False
        self._nick_forbidden.pop(member.id, None)
        return True

    async def set_break_nick(self, member: discord.Member, emoji: str | None) -> None:
        """ไปพัก → ใส่อีโมจิท้ายชื่อ / กลับ (emoji=None) → เอาออก"""
        self._break_emoji[member.id] = emoji
        try:
            await self.apply_nick(member)
        finally:
            if emoji is None:
                self._break_emoji.pop(member.id, None)

    async def sync_all_nicks(self) -> int:
        """ปรับชื่อพนักงานทุกคนให้ตรงกับเว็บวันนี้ + สถานะพัก"""
        changed = 0
        for g in self.guilds:
            for m in g.members:
                if str(m.id) not in NAME_BY_DISCORD:
                    continue                            # ไม่ใช่พนักงานในระบบ → ไม่แตะชื่อ
                if await self.apply_nick(m):
                    changed += 1
                    await asyncio.sleep(0.3)            # เว้นจังหวะ ไม่ยิง Discord รัวเกิน
        return changed

    async def _nick_sync_loop(self):
        """ทุก 5 นาที: อ่านตารางหน้าที่ใหม่ แล้วปรับชื่อทุกคน (ข้ามวัน/เปลี่ยนกะ/แอดมินแก้หน้าที่ → ชื่อเปลี่ยนตามเอง)"""
        await asyncio.sleep(20)                         # รอโหลดพนักงาน + รายชื่อคนที่พักอยู่ก่อน
        while True:
            try:
                if WEB_NICK_ENABLED:
                    await load_today_webs()
                n = await self.sync_all_nicks()
                if n:
                    logger.info(f"[NICK] ปรับชื่อ {n} คน (สีเว็บวันนี้ {sum(1 for v in TODAY_WEB.values() if v)} คน)")
            except Exception as e:
                logger.warning(f"[NICK] sync error: {e}")
            await asyncio.sleep(WEB_NICK_SYNC_EVERY)

    async def _sweep_once(self, full: bool) -> int:
        min_age = max(BREAK_MSG_DELETE_AFTER, 0) + 10
        now = datetime.now(timezone.utc)
        cutoff = time_module.time() - self.SWEEP_KEEP
        self._sweep_channels = {cid: t for cid, t in self._sweep_channels.items() if t >= cutoff}
        me_id = self.user.id if self.user else None
        removed = 0
        for g in self.guilds:
            for ch in [*g.voice_channels, *g.text_channels]:
                if not full and ch.id not in self._sweep_channels:
                    continue
                perms = ch.permissions_for(g.me)
                if not (perms.view_channel and perms.read_message_history):
                    continue
                try:
                    async for m in ch.history(limit=self.SWEEP_HISTORY):
                        parsed = parse_break_message(m, me_id)
                        if parsed is None or parsed[1] != "กลับที่นั่งแล้ว":
                            continue
                        if (now - m.created_at).total_seconds() < min_age:
                            # ยังไม่ถึงเวลาลบ → จำห้องนี้ไว้ ตรวจซ้ำรอบหน้า (สำคัญตอนเพิ่งรีสตาร์ท บอทจะยังตามลบให้)
                            self._sweep_channels[ch.id] = time_module.time()
                            continue
                        if m.reference and m.reference.message_id:
                            try:
                                await ch.get_partial_message(m.reference.message_id).delete()
                            except Exception:
                                pass
                        try:
                            await m.delete()
                            removed += 1
                        except Exception:
                            pass
                except Exception:
                    continue
        return removed

    async def send_status(self, message: str):
        """ส่งข้อความสถานะไปยัง DISCORD_STATUS_CHANNEL_ID ถ้ากำหนดไว้"""
        if not STATUS_CHANNEL_ID:
            return
        try:
            channel = self.get_channel(STATUS_CHANNEL_ID)
            if channel:
                now = get_thai_time().strftime("%d/%m %H:%M")
                await channel.send(f"{message}\n> 🕐 {now}")
        except Exception as e:
            logger.error(f"Failed to send status message: {e}")

    async def on_voice_state_update(self, member: discord.Member, before: discord.VoiceState, after: discord.VoiceState):
        now = time_module.time()
        current_hour = get_thai_time().hour

        # ขึ้นชั่วโมงใหม่ → reset สถิติทั้งหมด
        if current_hour != self._current_hour:
            self._current_hour = current_hour
            self._hour_time.clear()
            logger.info(f"New hour slot {current_hour:02d}:00 — resetting work channel stats")

        # บันทึกเวลาที่อยู่ในห้องก่อนหน้า
        if member.id in self._voice_join_time:
            prev_ch_id, join_ts = self._voice_join_time[member.id]
            duration = now - join_ts
            if not is_destination(prev_ch_id):
                self._hour_time[member.id][prev_ch_id] += duration

        # บันทึกห้องใหม่ที่เข้า
        if after.channel:
            self._voice_join_time[member.id] = (after.channel.id, now)
        else:
            self._voice_join_time.pop(member.id, None)

        # จำห้องทำงานล่าสุด (ห้องที่อยู่ตอนนี้ หรือห้องที่เพิ่งออกมา ถ้าไม่ใช่ห้องกินข้าว)
        if after.channel and not is_destination(after.channel.id):
            self._last_work_channel[member.id] = after.channel.id
        elif before.channel and not is_destination(before.channel.id):
            self._last_work_channel[member.id] = before.channel.id

    async def wait_until_ready_event(self):
        await self._ready_event.wait()

    # ห้อง Discord เพิ่ม/ลบ/เปลี่ยนชื่อ → ส่งรายชื่อห้องใหม่ให้หน้าเว็บนับแชททันที
    async def on_guild_role_update(self, before, after):
        self._nick_forbidden.clear()        # แอดมินแก้ role/ลำดับ role → ลองเปลี่ยนชื่อใหม่ได้ทันที

    async def on_member_update(self, before, after):
        if self.user and after.id == self.user.id and before.roles != after.roles:
            self._nick_forbidden.clear()    # บอทได้ role ใหม่

    async def on_guild_channel_create(self, channel):
        if chat_count is not None:
            chat_count.request_channel_publish()

    async def on_guild_channel_delete(self, channel):
        if chat_count is not None:
            chat_count.request_channel_publish()

    async def on_guild_channel_update(self, before, after):
        if chat_count is not None and (before.name != after.name or before.category_id != after.category_id
                                       or before.position != after.position):
            chat_count.request_channel_publish()

    async def on_message(self, message: discord.Message):
        # 📊 นับแชท — รับรีพลายตอบข้อความคำถาม (ดู bot/chat_count.py)
        if chat_count is None:
            return
        try:
            await chat_count.handle_message(message)
        except Exception as e:
            logger.error(f"[CHATCOUNT] on_message error: {e}")

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

    async def is_in_destination(self, discord_user_id: str) -> bool:
        """ตอนนี้คนนี้นั่งอยู่ในห้องกินข้าวหรือเปล่า"""
        _, member = await self.find_member(discord_user_id)
        ch = member.voice.channel if member is not None and member.voice else None
        return ch is not None and is_destination(ch.id)

    async def _pick_return_channel(self, candidates: list) -> discord.VoiceChannel | None:
        """เลือกห้องที่จะย้ายกลับ: ตัวแรกในรายการที่ยังมีอยู่จริง และไม่ใช่ห้องกินข้าว"""
        for c in candidates:
            if c is None:
                continue
            vc = c if isinstance(c, discord.VoiceChannel) else await self.find_voice_channel_by_id(str(c))
            if vc is not None and not is_destination(vc.id):
                return vc
        return None

    async def find_voice_channel_by_id(self, channel_id: str) -> discord.VoiceChannel | None:
        try:
            channel = self.get_channel(int(channel_id))
            if isinstance(channel, discord.VoiceChannel):
                return channel
        except Exception as e:
            logger.error(f"Failed to find channel by ID {channel_id}: {e}")
        return None

    def get_move_channel_id(self, activity: str) -> str:
        for keyword, channel_name in ACTIVITY_MOVE_CHANNEL.items():
            if keyword in activity:
                return channel_name
        return ""

    def channel_time_this_hour(self, member_id: int) -> dict[int, float]:
        """{channel_id: วินาที} ที่อยู่ในแต่ละห้องชั่วโมงนี้ = เวลาที่สะสมไว้ + session ที่กำลังอยู่ตอนนี้ (ไม่นับห้องปลายทาง)"""
        time_by_ch: dict[int, float] = dict(self._hour_time.get(member_id) or {})
        if member_id in self._voice_join_time:
            cur_ch_id, join_ts = self._voice_join_time[member_id]
            if not is_destination(cur_ch_id):
                time_by_ch[cur_ch_id] = time_by_ch.get(cur_ch_id, 0) + (time_module.time() - join_ts)
        return time_by_ch

    def get_work_channel_id(self, member_id: int) -> int | None:
        """คืน channel_id ที่อยู่นานสุดในชั่วโมงนี้ (ไม่นับห้องปลายทาง)"""
        time_by_ch = self.channel_time_this_hour(member_id)
        if not time_by_ch:
            return None
        return max(time_by_ch, key=lambda ch: time_by_ch[ch])

    async def _find_activity_msg_anywhere(self, guild, name: str):
        """หาข้อความ "**ชื่อ** ไป..." ล่าสุดที่ยังไม่มีคู่กลับที่นั่ง จากทุกห้องในเซิร์ฟเวอร์ (ย้อนหลัง 6 ชม.)"""
        if guild is None:
            return None
        since = datetime.now(timezone.utc) - timedelta(hours=6)
        sem = asyncio.Semaphore(5)              # ค้นหลายห้องพร้อมกัน (เดิมทีละห้อง ช้ามากตอนห้องเยอะ)

        async def search(ch):
            async with sem:
                return await self._find_last_activity_msg(ch, name, after=since, limit=60)

        chans = []
        for ch in [*guild.voice_channels, *guild.text_channels]:
            if is_destination(ch.id):
                continue
            perms = ch.permissions_for(guild.me)
            if perms.view_channel and perms.read_message_history:
                chans.append(ch)
        best = None
        for m in await asyncio.gather(*(search(ch) for ch in chans)):
            if m is not None and (best is None or m.created_at > best.created_at):
                best = m
        return best

    async def _find_last_activity_msg(self, channel, name: str, after=None, limit: int = 100):
        """หาข้อความแจ้ง "**ชื่อ** ไป..." ล่าสุดของบอทในห้อง (ใช้ตอนบอทรีสตาร์ทแล้วไม่มีข้อมูลในความจำ)"""
        needle = f"**{name}** ไป"
        try:
            async for m in channel.history(limit=limit, after=after, oldest_first=False):
                if m.author.id == self.user.id and needle in (m.content or ""):
                    return m
                if m.author.id == self.user.id and f"**{name}** กลับที่นั่งแล้ว" in (m.content or ""):
                    return None   # เจอข้อความกลับที่นั่งก่อน = ข้อความไปล่าสุดถูกจับคู่ไปแล้ว
        except Exception as e:
            logger.warning(f"ค้นข้อความแจ้งพักของ {name} ไม่ได้: {e}")
        return None

    async def send_notification(self, discord_user_id: str, name: str, activity: str,
                                 is_return: bool, group_name: str, timestamp: str | None = None) -> tuple[bool, str | None]:
        emoji = get_emoji(activity, is_return)
        action = f"**{name}** กลับที่นั่งแล้ว" if is_return else f"**{name}** ไป{activity}"
        # อ่านเวลาจากข้อความ Telegram เป็นเวลาไทย (ตัวเดียวกับที่เก็บลง DB)
        th_dt = parse_telegram_timestamp(timestamp) if timestamp else None
        if th_dt is not None:
            time_part = th_dt.strftime("%H:%M")
        elif timestamp:
            time_part = timestamp.split(" ")[-1][:5]   # อ่านไม่ออก → แสดงตามข้อความเดิม
        else:
            time_part = get_thai_time().strftime("%H:%M")
        message = f"{emoji} {action}\n> 🕐 {time_part}"           # (ตัดชื่อกลุ่มออก ให้ข้อความสั้นลง)

        guild, member = await self.find_member(discord_user_id)
        if member is None:
            logger.warning(f"No member found for {name} ({discord_user_id})")
            return False, None

        # 🏷️ อีโมจิหน้าชื่อ: ไป → ใส่ / กลับ → คืนชื่อเดิม (พังก็ไม่กระทบการแจ้ง/ย้ายห้อง)
        #    กินข้าว → ไม่ใส่ (และล้างอีโมจิเก่าถ้ามีค้าง) เพราะถูกย้ายไปห้องกินข้าวอยู่แล้ว
        try:
            nick_emoji = None if (is_return or emoji in NICK_SKIP_EMOJIS or not NICK_STATUS_ENABLED) else emoji
            await self.set_break_nick(member, nick_emoji)
        except Exception as e:
            logger.warning(f"[NICK] {name}: {e}")

        # ห้องทำงาน = ห้องที่อยู่นานสุดในชั่วโมงนี้
        work_ch_id = self.get_work_channel_id(member.id)
        work_vc = await self.find_voice_channel_by_id(str(work_ch_id)) if work_ch_id else None

        # ห้องที่ member อยู่ตอนนี้ (fallback ถ้ายังไม่มีสถิติ)
        current_ch = None
        if member.voice and member.voice.channel:
            ch = member.voice.channel
            if not is_destination(ch.id):
                current_ch = ch

        found_ref = None
        fallback_vc = None
        if is_return and member.id not in self._last_activity_message and member.id not in self._last_notified_channel:
            # บอทรีสตาร์ทระหว่างที่คนนี้พักอยู่ (ความจำหาย) → ไล่หาข้อความ "ไป..." ล่าสุดของเขาจากทุกห้อง
            # ห้องที่เจอ = ห้องที่แจ้งตอนไป (= ห้องทำงาน) ใช้ทั้งเป็นที่แจ้งกลับและห้องที่ย้ายกลับ
            found_ref = await self._find_activity_msg_anywhere(guild, name)
            if found_ref is not None:
                fallback_vc = found_ref.channel
                logger.info(f"[RESTORE] เจอข้อความไปของ {name} ใน #{fallback_vc.name} (หลังบอทรีสตาร์ท)")

        move_target = None
        if is_return:
            # กลับที่นั่ง → ย้ายกลับเมื่อ "ตอนนี้อยู่ในห้องกินข้าว" เท่านั้น
            # (ถ้าเดินกลับห้องเองแล้ว หรือออกจากห้องเสียงไปแล้ว ไม่ต้องย้าย)
            mem_vc = member.voice.channel if member.voice else None
            if mem_vc is not None and is_destination(mem_vc.id):
                if (member.id not in self._return_channel and member.id not in self._last_work_channel
                        and work_vc is None and fallback_vc is None):
                    # ไม่มีข้อมูลในความจำเลย (บอทรีสตาร์ทระหว่างกินข้าว) → หาห้องจากข้อความ "ไป..." ล่าสุด
                    found_ref = await self._find_activity_msg_anywhere(guild, name)
                    if found_ref is not None:
                        fallback_vc = found_ref.channel
                        logger.info(f"[RESTORE] เจอข้อความไปของ {name} ใน #{fallback_vc.name} (หลังบอทรีสตาร์ท)")
                # ลำดับความสำคัญ: ห้องที่อยู่ตอนถูกย้ายไปกินข้าว → ห้องทำงานล่าสุด → ห้องที่อยู่นานสุดชั่วโมงนี้
                #                 → ห้องที่แจ้ง "ไป..." (กรณีบอทรีสตาร์ท)
                move_target = await self._pick_return_channel([
                    self._return_channel.get(member.id),
                    self._last_work_channel.get(member.id),
                    work_vc,
                    fallback_vc if isinstance(fallback_vc, discord.VoiceChannel) else None,
                ])
                if move_target:
                    try:
                        await member.move_to(move_target)
                        logger.info(f"Moved {name} back → #{move_target.name}")
                    except discord.Forbidden:
                        logger.error(f"No permission to move {name} — bot needs 'Move Members' permission")
                    except Exception as e:
                        logger.error(f"Failed to move {name} back: {e}")
                else:
                    logger.warning(f"No work channel found for {name}, skipping return move")
            elif mem_vc is None:
                logger.info(f"{name} is not in a voice channel — skipping return move")
            else:
                logger.info(f"{name} is not in the dining room — skipping return move")
            self._return_channel.pop(member.id, None)

            # แจ้งในห้องที่เคยแจ้งกิจกรรมล่าสุด (ไม่มี → ห้องที่ย้ายกลับไป / ห้องที่อยู่ตอนนี้)
            last_ch_id = self._last_notified_channel.get(member.id)
            last_vc = await self.find_voice_channel_by_id(str(last_ch_id)) if last_ch_id else None
            notify_vc = last_vc or fallback_vc or move_target or current_ch

        else:
            target_channel_id = self.get_move_channel_id(activity)

            if target_channel_id:
                # กินข้าว / ทานข้าว → แจ้งในห้องทำงาน แล้วย้ายไป Dining
                notify_vc = work_vc or current_ch or await self._pick_return_channel(
                    [self._last_work_channel.get(member.id)])
                target_vc = await self.find_voice_channel_by_id(target_channel_id)
                if target_vc:
                    mem_vc = member.voice.channel if member.voice else None
                    if mem_vc is None:
                        # [FIX] ไม่ได้อยู่ห้องเสียง → ย้ายไม่ได้ (เดิมยิง move แล้ว error ทุกครั้ง)
                        logger.info(f"{name} is not in a voice channel, skipping move")
                    elif mem_vc.id == target_vc.id:
                        logger.info(f"{name} already in target channel '{target_vc.name}', skipping move")
                    else:
                        try:
                            await member.move_to(target_vc)
                            if not is_destination(mem_vc.id):
                                self._return_channel[member.id] = mem_vc.id   # จำห้องเดิมไว้ย้ายกลับ
                            logger.info(f"Moved {name} → #{target_vc.name} (ห้องเดิม #{mem_vc.name})")
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
                    ref_msg = self._last_activity_message.get(member.id) or found_ref
                    if ref_msg is None:
                        # บอทเพิ่งรีสตาร์ท (ความจำหาย) → หาข้อความ "ไป..." ล่าสุดของคนนี้จากประวัติห้อง
                        ref_msg = await self._find_last_activity_msg(notify_vc, name)
                    if ref_msg and ref_msg.channel.id == notify_vc.id:
                        sent_msg = await ref_msg.reply(message)
                    else:
                        sent_msg = await notify_vc.send(message)
                    self._last_activity_message.pop(member.id, None)
                    self._last_notified_channel.pop(member.id, None)   # จับคู่ไป-กลับครบแล้ว
                    self._sweep_channels[notify_vc.id] = time_module.time()   # ให้ตัวเก็บกวาดตามดูห้องนี้
                    # 🧹 กลับที่นั่งแล้ว → ลบชุดข้อความ (แจ้งไป + แจ้งกลับ) ออกจากห้อง หลังหน่วงเวลาให้เห็นสักครู่
                    if BREAK_MSG_DELETE_AFTER >= 0:
                        for msg in (ref_msg, sent_msg):
                            if msg is not None:
                                try:
                                    await msg.delete(delay=BREAK_MSG_DELETE_AFTER)
                                except Exception as e:
                                    logger.warning(f"ลบข้อความแจ้งพักไม่สำเร็จ: {e}")
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

# Telethon client ที่ต่ออยู่ตอนนี้ (ใช้ส่งสรุปนับแชทเข้ากลุ่ม Telegram ด้วยบัญชีเดียวกัน)
TG_CLIENT: TelegramClient | None = None

# 📊 นับแชท — ตั้งค่าในหน้า K36 แท็บ "นับแชท"
chat_count: ChatCount | None = (
    ChatCount(discord_bot, supabase, lambda: EMPLOYEES, lambda: TG_CLIENT,
              norm_chat_id, load_employees_from_supabase, staff_name_by_discord)
    if supabase else None
)


# ── 🧹 ล้างข้อความแจ้งพักเก่า ─────────────────────────────────────────
KEEP_OPEN_BREAK_HOURS = 3   # ข้อความ "ไป..." ที่ยังไม่มีคู่ "กลับที่นั่ง" และใหม่กว่านี้ = อาจยังพักอยู่ → เก็บไว้


async def _purge_break_messages(channel, since: datetime) -> int:
    """ลบข้อความแจ้งพักของบอทในห้องเดียว คืนจำนวนที่ลบ"""
    me = discord_bot.user
    now = datetime.now(timezone.utc)
    returned: set[str] = set()
    to_delete: list[discord.Message] = []
    me_id = me.id if me else None
    async for m in channel.history(after=since, limit=None, oldest_first=False):   # ใหม่ → เก่า (since=None = ทั้งหมด)
        parsed = parse_break_message(m, me_id)
        if parsed is None:
            continue
        name, kind = parsed
        if kind == "กลับที่นั่งแล้ว":
            to_delete.append(m)
            returned.add(name)
        elif name in returned:                         # "ไป..." ที่กลับมาแล้ว
            to_delete.append(m)
            returned.discard(name)
        elif (now - m.created_at).total_seconds() > KEEP_OPEN_BREAK_HOURS * 3600:
            to_delete.append(m)                        # ค้างนานแล้ว ถือว่าจบไปแล้ว
        # นอกนั้น = อาจยังพักอยู่ → ไม่ลบ

    return await delete_messages_safely(channel, to_delete)


_purge_running = False
# role ที่ใช้ /clear_break_messages ได้ (ชื่อ role คั่นด้วย , ไม่สนตัวพิมพ์ใหญ่/เล็ก)
CLEAR_ALLOWED_ROLES = {x.strip().lower() for x in os.environ.get("CLEAR_ALLOWED_ROLES", "PTT").split(",") if x.strip()}


@discord_bot.tree.command(name="clear_break_messages", description="ลบข้อความแจ้งพัก (ไป.../กลับที่นั่งแล้ว) ของบอทในทุกห้อง")
@discord.app_commands.describe(days="ย้อนหลังกี่วัน — เว้นว่าง = ลบทั้งหมดตั้งแต่แรก")
async def clear_break_messages_command(interaction: discord.Interaction, days: int = 0):
    global _purge_running
    # สิทธิ์: role ที่ตั้งใน env CLEAR_ALLOWED_ROLES (ค่าเริ่มต้น "PTT") หรือคนที่มีสิทธิ์ Manage Messages
    member = interaction.user if isinstance(interaction.user, discord.Member) else None
    role_names = {r.name.strip().lower() for r in (member.roles if member else [])}
    allowed_by_role = bool(role_names & CLEAR_ALLOWED_ROLES)
    allowed_by_perm = bool(member and member.guild_permissions.manage_messages)
    if not (allowed_by_role or allowed_by_perm):
        await interaction.response.send_message(
            "❌ คำสั่งนี้ใช้ได้เฉพาะ role " + ", ".join(sorted(CLEAR_ALLOWED_ROLES)).upper() + " หรือคนที่มีสิทธิ์จัดการข้อความ",
            ephemeral=True,
        )
        return
    if _purge_running:
        await interaction.response.send_message("⏳ กำลังลบรอบก่อนหน้าอยู่ รอให้เสร็จก่อนครับ", ephemeral=True)
        return
    days = max(0, int(days))
    since = datetime.now(timezone.utc) - timedelta(days=days) if days else None
    scope = f"ย้อนหลัง {days} วัน" if days else "ทั้งหมดตั้งแต่แรก"
    await interaction.response.send_message(
        f"🧹 เริ่มลบข้อความแจ้งพัก ({scope}) ในทุกห้องแล้ว\n"
        f"ข้อความเก่ากว่า 14 วัน Discord ให้ลบได้ทีละข้อความ อาจใช้เวลานาน — เสร็จแล้วบอทจะแจ้งในห้องนี้",
        ephemeral=True,
    )
    _purge_running = True
    spawn(_run_purge(interaction, since, scope))


async def _run_purge(interaction: discord.Interaction, since, scope: str):
    global _purge_running
    started = time_module.time()
    total, rooms, skipped = 0, 0, 0
    sem = asyncio.Semaphore(4)                         # ลบพร้อมกันได้หลายห้อง (rate limit ของ Discord แยกตามห้อง)

    async def one(ch, me_member):
        nonlocal total, rooms, skipped
        perms = ch.permissions_for(me_member)
        if not (perms.view_channel and perms.read_message_history):
            skipped += 1
            return
        async with sem:
            try:
                n = await _purge_break_messages(ch, since)
            except Exception as e:
                logger.warning(f"[CLEAR] #{ch.name}: {e}")
                skipped += 1
                return
        if n:
            total += n
            rooms += 1
            logger.info(f"[CLEAR] ลบข้อความแจ้งพัก {n} ข้อความใน #{ch.name}")

    try:
        guilds = [interaction.guild] if interaction.guild else discord_bot.guilds
        jobs = [one(ch, g.me) for g in guilds for ch in [*g.text_channels, *g.voice_channels]]
        await asyncio.gather(*jobs)
    finally:
        _purge_running = False

    mins = (time_module.time() - started) / 60
    msg = (f"🧹 ลบข้อความแจ้งพักเสร็จแล้ว ({scope}): **{total}** ข้อความ จาก {rooms} ห้อง · ใช้เวลา {mins:.1f} นาที\n"
           f"ข้อความ \"ไป...\" ที่ยังไม่กลับที่นั่งภายใน {KEEP_OPEN_BREAK_HOURS} ชม.ล่าสุด เก็บไว้ให้")
    if skipped:
        msg += f"\n⚠️ ข้าม {skipped} ห้องที่บอทอ่านประวัติไม่ได้"
    logger.info(f"[CLEAR] เสร็จ: {total} ข้อความ {rooms} ห้อง {mins:.1f} นาที")
    try:
        if time_module.time() - started < 14 * 60:
            await interaction.followup.send(msg, ephemeral=True)     # token ของคำสั่งใช้ได้ 15 นาที
        elif interaction.channel is not None:
            await interaction.channel.send(f"{interaction.user.mention} {msg}", delete_after=600)
    except Exception as e:
        logger.warning(f"[CLEAR] แจ้งผลไม่สำเร็จ: {e}")


@discord_bot.tree.command(name="status", description="ดูสถานะบอทและห้องทำงานของพนักงาน")
async def status_command(interaction: discord.Interaction):
    # จำกัดให้ใช้ได้เฉพาะ status channel เท่านั้น
    if STATUS_CHANNEL_ID and interaction.channel_id != STATUS_CHANNEL_ID:
        await interaction.response.send_message(
            "❌ คำสั่งนี้ใช้ได้เฉพาะใน status channel เท่านั้น", ephemeral=True
        )
        return

    th_now = get_thai_time()
    now = th_now.strftime("%d/%m %H:%M:%S")
    hour = th_now.hour
    lines = [f"🟢 **Bot Status** — `{now}`",
             f"⏱ Hour slot: `{hour:02d}:00 – {(hour+1)%24:02d}:00`",
             ""]

    # รวบรวมข้อมูลห้องทำงานของทุกคนที่มีสถิติ
    work_info: list[tuple[str, str, float]] = []  # (name, channel_name, seconds)
    # ทุกคนที่มีสถิติชั่วโมงนี้ + คนที่อยู่ในห้องเสียงตอนนี้ (รวม session ที่กำลังอยู่)
    member_ids = set(discord_bot._hour_time) | set(discord_bot._voice_join_time)
    for member_id in member_ids:
        total = discord_bot.channel_time_this_hour(member_id)
        if not total:
            continue

        best_ch_id = max(total, key=lambda c: total[c])
        best_secs = total[best_ch_id]

        # หาชื่อห้องและชื่อพนักงาน
        ch_obj = discord_bot.get_channel(best_ch_id)
        ch_name = f"#{ch_obj.name}" if ch_obj else f"ID:{best_ch_id}"
        disc_id = str(member_id)
        name = staff_name_by_discord(disc_id) or disc_id
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
    """ตอน startup โหลดการพักที่ยังเปิดอยู่ (จากตารางหลัก break_logs) กลับเข้า _currently_out"""
    global _out_restored
    if not supabase:
        _out_restored = True
        return
    try:
        now = get_thai_time()
        break_date = get_break_date_from_time(now)
        prev_date = (now - timedelta(days=1)).strftime("%Y-%m-%d")
        res = await sb(lambda: supabase.from_(BREAK_TABLES[0])
                       .select("staff_name")
                       .in_("break_date", [break_date, prev_date])
                       .is_("break_end", "null")
                       .execute())
        if not res.data:
            logger.info("[Supabase] ไม่มี open break sessions ค้างอยู่")
            _out_restored = True
            return
        restored = 0
        for r in res.data:
            tid = TID_BY_NAME.get(r["staff_name"])
            if tid and tid not in _currently_out:
                _currently_out[tid] = ""  # ไม่รู้ group_name เดิม
                restored += 1
        logger.info(f"[Supabase] Restored {restored} open break sessions → _currently_out")
        _out_restored = True
    except Exception as e:
        logger.error(f"[Supabase] supabase_restore_open_breaks error: {e}")


_bg_started = False


def start_background_sync() -> None:
    """เริ่มงานเบื้องหลังที่ต้องมีแค่ชุดเดียว (เรียกซ้ำได้ จะไม่สร้างซ้ำ)"""
    global _bg_started
    if _bg_started or not supabase:
        return
    _bg_started = True
    spawn(watch_realtime_forever())
    spawn(poll_data_forever())


async def reload_all_data() -> None:
    await load_employees_from_supabase()
    await load_target_groups()


async def poll_data_forever() -> None:
    """⏱️ กันเหนียว: รีโหลดพนักงาน+กลุ่มทุก 5 นาที "เสมอ" — แม้ Realtime ต่อได้แต่ไม่มี event วิ่งมา
    (เช่น ตาราง users ยังไม่ถูก add เข้า publication supabase_realtime ฝั่ง K36)
    ข้อมูลจะตามทันภายใน 5 นาทีโดยไม่ต้องรีสตาร์ทบอท (ถ้า Realtime ทำงาน จะเห็นทันทีเหมือนเดิม)"""
    while True:
        await asyncio.sleep(300)  # 5 นาที
        try:
            await reload_all_data()
        except Exception as e:
            logger.error(f"[Poll] reload error: {e}")


REALTIME_DOWN_LIMIT = 60   # หลุดนานเกินกี่วินาที (หลังไลบรารีลองต่อเองแล้ว) ถึงสร้าง connection ใหม่


async def watch_realtime_forever() -> None:
    """ดักการเปลี่ยนแปลงตาราง users และ telegram_groups ผ่าน Supabase Realtime → รีโหลดทันที

    [FIX] เดิมแยกเป็น 2 ฟังก์ชันที่เขียนไม่เหมือนกัน: ตัว users จับไม่ได้ว่าหลุด (วน sleep เฉยๆ)
    ตัว telegram_groups ไม่มี reconnect เลย — ตอนนี้ใช้ connection เดียว ฟัง 2 ตาราง
    และเช็คสถานะทุก 5 วิ ถ้าหลุดนานเกินกำหนดจะต่อใหม่ แล้วรีโหลดข้อมูลชดเชย event ที่พลาดไป"""
    try:
        from realtime import AsyncRealtimeClient
    except ImportError:
        logger.warning("[EMPLOYEES] ไม่มี realtime library → poll ทุก 30 วินาที")
        while True:
            await asyncio.sleep(30)
            await load_employees_from_supabase()

    realtime_url = _supabase_url.replace("https://", "wss://") + "/realtime/v1"
    loop = asyncio.get_running_loop()

    # callback ต้องเป็นฟังก์ชันธรรมดา ไม่ใช่ async def
    # ไลบรารี realtime เรียกแบบ binding.callback(payload, ref) เฉยๆ ไม่ได้ await
    def make_callback(log_msg: str, reload_fn):
        def callback(*_args, **_kwargs):
            logger.info(log_msg)
            asyncio.run_coroutine_threadsafe(reload_fn(), loop)
        return callback

    first = True
    while True:
        rt_client = None
        try:
            rt_client = AsyncRealtimeClient(realtime_url, _supabase_key)
            await rt_client.connect()
            await rt_client.channel("users-changes").on_postgres_changes(
                event="*", schema="public", table="users",
                callback=make_callback("[EMPLOYEES] Realtime: ตรวจพบการเปลี่ยนแปลง → รีโหลด",
                                       load_employees_from_supabase),
            ).subscribe()
            await rt_client.channel("groups-changes").on_postgres_changes(
                event="*", schema="public", table="telegram_groups",
                callback=make_callback("[GROUPS] Realtime: ตรวจพบการเปลี่ยนแปลงใน telegram_groups → รีโหลดทันที",
                                       load_target_groups),
            ).subscribe()
            logger.info("[REALTIME] กำลัง watch ตาราง users + telegram_groups...")
            if not first:
                await reload_all_data()          # ระหว่างหลุดอาจพลาด event ไป → โหลดใหม่ให้ทันข้อมูลล่าสุด
            first = False

            down_since = None
            while True:
                await asyncio.sleep(5)
                if rt_client.is_connected:
                    down_since = None
                    continue
                down_since = down_since or time_module.time()
                if time_module.time() - down_since > REALTIME_DOWN_LIMIT:
                    raise ConnectionError(f"หลุดเกิน {REALTIME_DOWN_LIMIT} วินาที")
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.error(f"[REALTIME] disconnected: {e} → reconnect in 10s")
        finally:
            if rt_client is not None:
                try:
                    await rt_client.close()
                except Exception:
                    pass
        await asyncio.sleep(10)


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
    global TG_CLIENT
    TG_CLIENT = client
    await discord_bot.wait_until_ready_event()
    # โหลดกลุ่ม Telegram จาก Supabase K36
    await load_target_groups()
    # โหลดพนักงานจาก Supabase K36
    await load_employees_from_supabase()
    await supabase_restore_open_breaks()
    await discord_bot.send_status("🟢 **Bot online** — Telegram + Discord connected")

    # Realtime + poll สำรอง เริ่มครั้งเดียวตลอดอายุโปรแกรม
    # [FIX] เดิมสร้างใหม่ทุกครั้งที่ Telegram reconnect โดยไม่ปิดตัวเก่า → connection/poller ซ้อนกันเรื่อยๆ
    start_background_sync()

    @client.on(events.NewMessage())
    async def handler(event):
        try:
            # ตัดจบตั้งแต่บรรทัดแรก — event.chat_id มีมาให้ในตัว event อยู่แล้ว
            # ไม่ต้องเรียก get_chat() (ซึ่งบางครั้งต้องยิงถาม Telegram กลับไป) ทุกข้อความ
            chat_id = norm_chat_id(getattr(event, "chat_id", None))

            group = GROUPS_BY_CHAT.get(chat_id)
            if not group:
                if not getattr(event, "is_private", False):
                    await log_unknown_chat(event, chat_id)
                return

            group_name = group["name"]

            text = event.message.text or ""
            sender = getattr(event.message.sender, "username", None) or getattr(event.message.sender, "first_name", "unknown") if event.message.sender else "unknown"
            logger.info(f"[Jun88] from {sender}: {text[:120]!r}")

            # กลุ่มกะงาน → ข้าม ไม่ประมวลผล
            # (ตัดฟีเจอร์เสียงขึ้นกะ + เตือนถ่ายรูปเช็คชื่อออกแล้ว)
            if chat_id in SHIFT_CHAT_IDS:
                return
            # กลุ่มเช็คอินปกติ → parse และแจ้ง Discord
            parsed = parse_message(text, group_name, chat_id)
            if parsed:
                logger.info(f"[PARSE OK] id={parsed['telegram_id']} activity={parsed['activity']}")
                await on_activity(parsed)
            elif "รหัสผู้ใช้" in text:
                # กรณีกลับที่นั่งล้มเหลวเพราะบอทเทเลแกรมรีเซท
                is_failed_return = any(kw in text for kw in RETURN_KEYWORDS) and "ไม่มีกิจกรรม" in text
                if is_failed_return:
                    tid_match = TID_RE.search(text)
                    if tid_match:
                        tid = tid_match.group(1)
                        emp = EMPLOYEES.get(tid)
                        if emp:
                            # ปิด break session ใน Supabase เสมอ ไม่ว่าจะอยู่ใน _currently_out หรือไม่
                            # (กรณีบอท Telegram รีเซทข้ามตี 1 ทำให้ _currently_out ว่าง)
                            async with person_lock(emp["name"]):
                                await _handle_failed_return(tid, emp, group_name)
                else:
                    logger.warning(f"[PARSE FAIL] Has รหัสผู้ใช้ but parse failed. Full text: {text!r}")
        except Exception as e:
            logger.error(f"Error handling Telegram message: {e}")

    logger.info(f"[GROUPS] เริ่มฟัง {len(GROUPS_BY_CHAT)} กลุ่ม (จับคู่ด้วย chat_id)")
    if not LOG_UNKNOWN_GROUPS:
        logger.info("[GROUPS] ปิด log กลุ่มที่ไม่ได้ตั้งค่าไว้ — ถ้าจะหา chat_id ใหม่ ตั้ง env LOG_UNKNOWN_GROUPS=1 แล้วรีสตาร์ท")
    try:
        await client.run_until_disconnected()
    finally:
        if TG_CLIENT is client:
            TG_CLIENT = None
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

    async with person_lock(emp["name"]):
        await _handle_activity(emp, parsed)


async def _handle_activity(emp: dict, parsed: dict) -> None:
    tid = parsed["telegram_id"]

    # Track activity state
    if parsed["is_return"]:
        _currently_out.pop(tid, None)
        # ปิด break session ใน Supabase ด้วยเวลาจาก Telegram
        await supabase_close_break(emp["name"], parsed.get("timestamp"))
    else:
        _currently_out[tid] = parsed.get("chat_id", "")
        # เปิด break session ใน Supabase ด้วยเวลาจาก Telegram
        await supabase_open_break(emp["name"], parsed["activity"], parsed.get("timestamp"))

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


async def _handle_failed_return(tid: str, emp: dict, group_name: str) -> None:
    """กด "กลับที่นั่ง" แต่บอทเช็คอินฝั่ง Telegram ตอบว่าไม่มีกิจกรรม (บอท Telegram รีเซท)"""
    await supabase_close_break(emp["name"], None)
    # [FIX] เดิมย้ายกลับ/แจ้งเฉพาะคนที่อยู่ใน _currently_out
    #       ถ้าบอทจำไม่ได้ แต่คนนั้นยังนั่งอยู่ห้องกินข้าว → ก็ต้องย้ายกลับด้วย
    await discord_bot.wait_until_ready_event()
    if tid in _currently_out or await discord_bot.is_in_destination(emp["discord_id"]):
        await discord_bot.send_notification(emp["discord_id"], emp["name"], "กลับที่นั่ง", True, group_name)
        _currently_out.pop(tid, None)
    logger.info(f"[FAILED RETURN] {emp['name']} — closed break session (bot reset case)")


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


def _reset_discord_client() -> None:
    """[FIX] ทำให้ client ที่ close ไปแล้วกลับมา start() ใหม่ได้
    เดิมเรียก start() ซ้ำกับ client ที่ปิดแล้ว → จบทันทีโดยไม่ต่อจริง บอทตายถาวรแต่ log วนบอกว่ากำลัง reconnect
    clear() อย่างเดียวไม่พอ ต้องทิ้ง connector ที่ปิดแล้ว และคืนค่า loop ให้ login() ตั้งค่าใหม่ด้วย"""
    from discord.utils import MISSING
    discord_bot.clear()
    connector = getattr(discord_bot.http, "connector", MISSING)
    if connector is not MISSING and getattr(connector, "closed", False):
        discord_bot.http.connector = MISSING
    if discord_bot.loop is MISSING:
        discord_bot.loop = discord.client._loop


async def start_discord_with_reconnect(token: str):
    """Wrap discord bot start with auto-reconnect on disconnect."""
    backoff = 5
    while True:
        try:
            if discord_bot.is_closed():
                _reset_discord_client()
            await discord_bot.start(token)
            logger.warning("Discord disconnected — reconnecting in %ds...", backoff)
        except Exception as e:
            logger.error("Discord error: %s — reconnecting in %ds...", e, backoff)
            try:
                await discord_bot.close()       # ปิด session เดิมให้สะอาดก่อนต่อใหม่ (กัน connection รั่ว)
            except Exception:
                pass
        await asyncio.sleep(backoff)
        backoff = min(backoff * 2, 300)


async def main():
    discord_token = os.environ.get("DISCORD_BOT_TOKEN", "")
    if not discord_token:
        logger.warning("Missing DISCORD_BOT_TOKEN. Discord bot disabled.")
        # [FIX] ปลดล็อกให้ Telegram ทำงานต่อได้ (บันทึกพักลง Supabase) — เดิมค้างรอ Discord ตลอดไป
        discord_bot._ready_event.set()
        discord_task = asyncio.create_task(asyncio.sleep(0))
    else:
        discord_task = asyncio.create_task(start_discord_with_reconnect(discord_token))

    telegram_task = asyncio.create_task(start_telegram_with_reconnect(on_activity))

    tasks = [discord_task, telegram_task]
    if discord_token and chat_count is not None:
        tasks.append(asyncio.create_task(chat_count.run()))
    else:
        logger.warning("[CHATCOUNT] นับแชทปิดอยู่ (ต้องมีทั้ง DISCORD_BOT_TOKEN และ SUPABASE_URL/KEY)")

    await asyncio.gather(*tasks, return_exceptions=True)


if __name__ == "__main__":
    asyncio.run(main())
