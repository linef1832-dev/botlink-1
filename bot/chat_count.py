"""
📊 นับแชท

การไหลของงาน
    1. K36 (แท็บ "นับแชท" หน้า Discord) ตั้งรอบเวลา + ติ๊กห้อง + เลือกเว็บ + ตั้งแพทเทิร์นข้อความ
       → ตาราง chatcount_schedules
    2. ถึงเวลา บอทสร้างรอบใน chatcount_rounds แล้วส่งข้อความคำถามเข้าทุกห้องที่ติ๊ก
       (กด "ส่งเลยตอนนี้" ในเว็บ = เว็บสร้างรอบเอง แล้วบอทส่งให้ภายในไม่กี่วินาที)
    3. พนักงานรีพลายข้อความคำถาม เช่น "MK8 15 JL69 3" → บอทแยกชื่อเว็บ+ตัวเลข เก็บลง chatcount_answers
       - ชื่อเว็บต้องตรงทุกตัวอักษร (รวมตัวพิมพ์ใหญ่/เล็ก) ถ้ามีชื่อไหนผิด → ไม่รับทั้งข้อความ แจ้งให้พิมพ์ใหม่
       - ไม่ผูกเว็บกับห้อง (เว็บย้ายห้องได้ทุกวัน) — นับความครบจาก "เว็บ"
       - ห้องเดิมตอบซ้ำ = แก้ค่า (ใช้อันล่าสุด)
       - เว็บเดียวกันถูกตอบจาก 2 ห้อง = ตอบซ้อน → รอแอดมินเลือกค่าที่ถูกในเว็บ
    4. ครบทุกเว็บ (หรือแอดมินกด "ส่งเท่าที่มี") → จัดข้อความตามแพทเทิร์น
       → ส่งเข้ากลุ่ม Telegram ผ่านบัญชี Telethon ตัวเดียวกับที่บอทใช้ดักเช็คอินอยู่แล้ว
    5. ส่ง Telegram สำเร็จ → ลบข้อความคำถาม + รีพลายของพนักงาน + ข้อความตอบกลับของบอท ออกจากห้อง Discord
       (บอทต้องมีสิทธิ์ Manage Messages ในห้อง ไม่งั้นลบได้แค่ข้อความของบอทเอง)
"""
from __future__ import annotations

import asyncio
import logging
import difflib
import re
import time as time_module
from collections import defaultdict
from datetime import date, datetime, timedelta, timezone
from typing import Awaitable, Callable

import discord

logger = logging.getLogger("bot.chatcount")

TH_TZ = timezone(timedelta(hours=7))
SETTINGS_TABLE = "chatcount_settings"
SCHEDULES_TABLE = "chatcount_schedules"
ROUNDS_TABLE = "chatcount_rounds"
ANSWERS_TABLE = "chatcount_answers"

SYNC_INTERVAL = 15            # วินาที
FIRE_WINDOW = 10 * 60         # ถ้าบอทเพิ่งรีสตาร์ท ยังยิงรอบที่เลยเวลามาไม่เกิน 10 นาทีให้
CHANNEL_PUBLISH_EVERY = 300   # ส่งรายชื่อห้อง Discord ให้เว็บทุก 5 นาที (+ ส่งทันทีเมื่อมีห้องเพิ่ม/ลบ/เปลี่ยนชื่อ)
TG_GROUPS_PUBLISH_EVERY = 120 # ส่งรายชื่อกลุ่ม Telegram ที่บัญชีอยู่ให้เว็บทุก 2 นาที
ROUND_LOOKBACK_DAYS = 2       # รอบที่ยังเปิดอยู่ ย้อนหลังกี่วันที่ยังรับรีพลาย

DEFAULT_ASK = (
    "📊 **นับแชท {รอบ}** · {วันที่}\n"
    "รีพลายข้อความนี้ บอกชื่อเว็บ + จำนวนแชท\n"
    "ตัวอย่าง: `MK8 15`  `JL69 3`"
)
DEFAULT_REPORT = "ตรวจสอบจำนวนแชท{รอบ}\nเวลา {เวลา} น\n\n{รายการ}"
DEFAULT_LINE = "{สี}เว็บ {เว็บ}{สี}\nไลน์หลัก {จำนวน} แชท\n"
DEFAULT_MISSING = "{สี}เว็บ {เว็บ}{สี}\nไลน์หลัก ยังไม่ตอบ\n"

MessageableChannel = (discord.TextChannel, discord.VoiceChannel, discord.Thread)

# หัวข้อความที่บอทใช้ตอบรีพลายนับแชท (ใช้หาข้อความตอบกลับที่ข้อความต้นฉบับถูกลบไปแล้ว ตอนเก็บกวาด)
_BOT_REPLY_PREFIXES = (
    "✅ รับแล้ว", "❌ ชื่อเว็บไม่ถูกต้อง", "⚠️ ไม่เจอชื่อเว็บ", "⚠️ ไม่ได้อยู่ในรอบนี้",
    "❌ บันทึกไม่สำเร็จ", "ℹ️ รอบนี้",
)

# "ชื่อ + ตัวเลข": "MK8 15", "MK8:15", "MK8 = 15", "MK8 แชท 15", "MK8 15แชท"
# ชื่อ = ตัวอักษรอังกฤษนำหน้า ตามด้วยอักษร/ตัวเลข (ชื่อเว็บทุกตัวขึ้นต้นด้วยตัวอักษร)
_PAIR_RE = re.compile(
    r"(?<![A-Za-z])([A-Za-z][A-Za-z0-9]*)"
    r"\s*(?:[:=\-–]\s*)?(?:แชท|chat|Chat|CHAT)?\s*(?:[:=\-–]\s*)?(\d{1,5})"
)
_IGNORE_WORDS = {"chat"}   # คำที่ไม่ใช่ชื่อเว็บ เช่น "chat 15"


# ----------------------------------------------------------------------
# ฟังก์ชันล้วน (ไม่ยุ่งกับ Discord/DB) — ทดสอบแยกได้
# ----------------------------------------------------------------------
def web_name_list(webs: list) -> list[str]:
    out = []
    for w in webs or []:
        name = str((w.get("name") if isinstance(w, dict) else w) or "").strip()
        if name and name not in out:
            out.append(name)
    return out


def parse_reply(text: str, web_names: list[str]) -> tuple[dict[str, int], list[tuple[str, str | None]]]:
    """แยก "ชื่อเว็บ ตัวเลข" ออกจากข้อความรีพลาย — ชื่อต้องตรงทุกตัวอักษร

    คืน (found {ชื่อเว็บ: จำนวน}, invalid [(คำที่พิมพ์มา, ชื่อที่น่าจะหมายถึง หรือ None)])
    """
    found: dict[str, int] = {}
    invalid: list[tuple[str, str | None]] = []
    exact = set(web_names)
    by_lower = {n.lower(): n for n in web_names}
    for m in _PAIR_RE.finditer(text or ""):
        token, num = m.group(1), int(m.group(2))
        if token in exact:
            found[token] = num
            continue
        if token.lower() in _IGNORE_WORDS:
            continue
        guess = by_lower.get(token.lower())
        if not guess:
            close = difflib.get_close_matches(token.upper(), [n.upper() for n in web_names], n=1, cutoff=0.6)
            guess = by_lower.get(close[0].lower()) if close else None
        if all(t != token for t, _ in invalid):
            invalid.append((token, guess))
    return found, invalid


def web_emoji_map(webs: list) -> dict[str, str]:
    """{ชื่อเว็บ: อีโมจิประจำเว็บ} ใช้แทน {สี} ในแพทเทิร์น"""
    out = {}
    for w in webs or []:
        if isinstance(w, dict) and w.get("name"):
            out[str(w["name"]).strip()] = str(w.get("emoji") or "").strip()
    return out


def thai_time(t) -> str:
    """'12:00' → '12.00' (รูปแบบเวลาแบบไทย)"""
    return str(t or "").replace(":", ".")


def render(template: str, values: dict) -> str:
    out = template or ""
    for k, v in values.items():
        out = out.replace("{" + k + "}", str(v))
    return out


def thai_date(d) -> str:
    if isinstance(d, str):
        d = date.fromisoformat(d[:10])
    return f"{d.day:02d}/{d.month:02d}/{d.year + 543}"


def evaluate_round(rnd: dict, answers: list[dict]) -> dict:
    """คำนวณสถานะรอบจากคำตอบทั้งหมด

    คืน {values: {เว็บ: answer}, conflicts: {เว็บ: [answer,...]}, missing: [เว็บ], complete: bool}
    """
    latest: dict[tuple[str, str], dict] = {}
    for a in sorted(answers, key=lambda x: (str(x.get("created_at") or ""), int(x.get("id") or 0))):
        latest[(a["web"], str(a.get("channel_id") or ""))] = a   # ห้องเดิมตอบซ้ำ → ใช้อันล่าสุด

    per_web: dict[str, list[dict]] = defaultdict(list)
    for (web, _ch), a in latest.items():
        per_web[web].append(a)

    resolved = rnd.get("resolved") or {}
    values: dict[str, dict] = {}
    conflicts: dict[str, list[dict]] = {}
    for web, lst in per_web.items():
        if len(lst) == 1:
            values[web] = lst[0]
            continue
        pick = next((a for a in lst if str(a.get("channel_id")) == str(resolved.get(web))), None)
        if pick:
            values[web] = pick
        else:
            conflicts[web] = lst

    expected = list(rnd.get("expected_webs") or [])
    missing = [w for w in expected if w not in values and w not in conflicts]
    complete = bool(expected) and not missing and not any(w in conflicts for w in expected)
    return {"values": values, "conflicts": conflicts, "missing": missing, "complete": complete}


def build_report(rnd: dict, ev: dict, force: bool, emojis: dict | None = None) -> str:
    emojis = emojis or {}
    expected = list(rnd.get("expected_webs") or [])
    line_t = rnd.get("line_template") or DEFAULT_LINE
    miss_t = rnd.get("missing_template") or DEFAULT_MISSING

    lines: list[str] = []
    total = 0
    answered = 0
    still_missing: list[str] = []
    for web in expected:
        a = ev["values"].get(web)
        if a is None and force and web in ev["conflicts"]:
            # ส่งเท่าที่มีทั้งที่ยังตอบซ้อน → ใช้คำตอบล่าสุด
            a = max(ev["conflicts"][web], key=lambda x: str(x.get("created_at") or ""))
        if a is not None:
            cnt = int(a.get("count") or 0)
            total += cnt
            answered += 1
            lines.append(render(line_t, {
                "เว็บ": web,
                "สี": emojis.get(web, ""),
                "จำนวน": cnt,
                "ห้อง": a.get("channel_name") or "",
                "ผู้ตอบ": a.get("staff_name") or a.get("discord_name") or "",
            }))
        else:
            still_missing.append(web)
            if miss_t.strip():
                lines.append(render(miss_t, {"เว็บ": web, "สี": emojis.get(web, "")}))

    return render(rnd.get("report_template") or DEFAULT_REPORT, {
        "วันที่": thai_date(rnd["round_date"]),
        "รอบ": rnd.get("label") or rnd.get("round_time") or "",
        "เวลา": thai_time(rnd.get("round_time")),
        "รายการ": "\n".join(lines),
        "รวม": total,
        "ตอบแล้ว": answered,
        "ทั้งหมด": len(expected),
        "ยังไม่ตอบ": ", ".join(still_missing) or "-",
    }).strip()


def build_ask(rnd: dict) -> str:
    return render(rnd.get("ask_template") or DEFAULT_ASK, {
        "รอบ": rnd.get("label") or rnd.get("round_time") or "",
        "วันที่": thai_date(rnd["round_date"]),
        "เวลา": thai_time(rnd.get("round_time")),
        "เว็บทั้งหมด": ", ".join(rnd.get("expected_webs") or []),
    }).strip()[:1900]


# ----------------------------------------------------------------------
# ตัวจัดการหลัก
# ----------------------------------------------------------------------
class ChatCount:
    def __init__(
        self,
        bot: discord.Client,
        supabase,
        get_employees: Callable[[], dict],
        get_tg_client: Callable[[], object | None],
        norm_chat_id: Callable[[object], str],
        load_employees: Callable[[], Awaitable[None]] | None = None,
    ):
        self.bot = bot
        self.db = supabase
        self.get_employees = get_employees
        self.get_tg_client = get_tg_client
        self.norm_chat_id = norm_chat_id
        self.load_employees = load_employees

        self.webs: list = []
        self.web_names: list[str] = []
        self.rounds: dict[int, dict] = {}
        self.msg_map: dict[int, int] = {}          # message_id คำถาม → round_id
        self._fired: set[tuple[int, str]] = set()  # (schedule_id, วันที่) ที่สร้างรอบแล้ว
        self._send_lock = asyncio.Lock()
        self._last_channel_publish = 0.0
        self._last_tg_publish = 0.0
        self._last_tg_groups: list = []
        self._publish_lock = asyncio.Lock()
        self._last_channels: list = []
        self._tg_cache: dict[str, object] = {}
        self._tg_cache_owner = None

    # ---------------- ตัวช่วย ----------------
    async def _db(self, fn):
        """supabase เป็นแบบ sync → รันใน thread แยก กันบอท Discord ค้าง"""
        return await asyncio.to_thread(fn)

    def _resolve_channel(self, channel_id):
        try:
            ch = self.bot.get_channel(int(str(channel_id).strip()))
        except (TypeError, ValueError):
            return None
        return ch if isinstance(ch, MessageableChannel) else None

    def _staff_name(self, discord_id) -> str | None:
        did = str(discord_id)
        for emp in (self.get_employees() or {}).values():
            if str(emp.get("discord_id")) == did:
                return emp.get("name")
        return None

    async def _update_round(self, rid: int, patch: dict) -> None:
        try:
            await self._db(lambda: self.db.from_(ROUNDS_TABLE).update(patch).eq("id", rid).execute())
        except Exception as e:
            logger.error(f"[CHATCOUNT] update round {rid} error: {e}")

    async def _fetch_answers(self, rid: int) -> list[dict]:
        res = await self._db(lambda: self.db.from_(ANSWERS_TABLE).select("*").eq("round_id", rid)
                             .order("created_at").execute())
        return res.data or []

    # ---------------- ลูปหลัก ----------------
    async def run(self) -> None:
        if hasattr(self.bot, "wait_until_ready_event"):
            await self.bot.wait_until_ready_event()
        else:
            await self.bot.wait_until_ready()
        logger.info(f"[CHATCOUNT] เริ่มทำงาน (เช็คทุก {SYNC_INTERVAL} วินาที)")
        while True:
            try:
                await self.sync_once()
            except Exception as e:
                logger.error(f"[CHATCOUNT] sync error: {e}")
            await asyncio.sleep(SYNC_INTERVAL)

    async def sync_once(self) -> None:
        if self.load_employees and not self.get_employees():
            await self.load_employees()

        res = await self._db(lambda: self.db.from_(SETTINGS_TABLE).select("webs").eq("id", 1).execute())
        self.webs = (res.data or [{}])[0].get("webs") or []
        self.web_names = web_name_list(self.webs)

        if time_module.time() - self._last_channel_publish > CHANNEL_PUBLISH_EVERY:
            await self.publish_channels()
        if time_module.time() - self._last_tg_publish > TG_GROUPS_PUBLISH_EVERY:
            await self.publish_tg_groups()

        await self._schedule_tick()

        since = (datetime.now(TH_TZ).date() - timedelta(days=ROUND_LOOKBACK_DAYS)).isoformat()
        res = await self._db(lambda: self.db.from_(ROUNDS_TABLE).select("*").eq("status", "open")
                             .gte("round_date", since).execute())
        rows = res.data or []
        self.rounds = {int(r["id"]): r for r in rows}
        self._rebuild_msg_map()

        for r in rows:
            if not r.get("posted"):
                await self._post_round(r)
        for r in rows:
            if r.get("posted"):
                await self.evaluate_and_send(int(r["id"]))

    def _rebuild_msg_map(self) -> None:
        m: dict[int, int] = {}
        for rid, r in self.rounds.items():
            for msg in r.get("messages") or []:
                try:
                    m[int(msg["message_id"])] = rid
                except (KeyError, TypeError, ValueError):
                    pass
        self.msg_map = m

    def request_channel_publish(self) -> None:
        """เรียกจาก event ของ Discord ตอนมีห้องเพิ่ม/ลบ/เปลี่ยนชื่อ → ส่งรายชื่อใหม่ให้เว็บทันที"""
        asyncio.create_task(self.publish_channels())

    async def publish_channels(self) -> None:
        async with self._publish_lock:
            await self._publish_channels()

    async def _publish_channels(self) -> None:
        """ส่งรายชื่อห้อง Discord ไปให้หน้าเว็บใช้ทำช่องติ๊ก"""
        chans = []
        for g in self.bot.guilds:
            for ch in g.channels:
                if not isinstance(ch, (discord.TextChannel, discord.VoiceChannel)):
                    continue
                cat = ch.category
                chans.append({
                    "id": str(ch.id),
                    "name": ch.name,
                    "type": "voice" if isinstance(ch, discord.VoiceChannel) else "text",
                    "category": cat.name if cat else "",
                    "cat_pos": cat.position if cat else -1,
                    "pos": ch.position,
                    "guild": g.name,
                })
        chans.sort(key=lambda c: (c["guild"], c["cat_pos"], c["type"] == "voice", c["pos"]))
        self._last_channel_publish = time_module.time()
        if chans == self._last_channels:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self._db(lambda: self.db.from_(SETTINGS_TABLE)
                           .update({"channels": chans, "channels_updated_at": now}).eq("id", 1).execute())
            self._last_channels = chans
            logger.info(f"[CHATCOUNT] อัปเดตรายชื่อห้อง {len(chans)} ห้อง")
        except Exception as e:
            logger.error(f"[CHATCOUNT] publish channels error: {e}")

    async def publish_tg_groups(self) -> None:
        """ส่งรายชื่อกลุ่ม Telegram ทั้งหมดที่บัญชี Telethon อยู่ ไปให้หน้าเว็บเลือกปลายทาง"""
        client = self.get_tg_client()
        if client is None:
            return                      # Telegram ยังไม่ต่อ → ลองใหม่รอบหน้า
        self._last_tg_publish = time_module.time()
        groups = []
        try:
            if self._tg_cache_owner is not client:
                self._tg_cache, self._tg_cache_owner = {}, client
            async for d in client.iter_dialogs():
                self._tg_cache[self.norm_chat_id(d.id)] = d.entity     # ใช้ส่งข้อความได้เลยไม่ต้องค้นซ้ำ
                if d.is_group:
                    groups.append({"id": str(d.id), "name": d.name or str(d.id)})
        except Exception as e:
            logger.error(f"[CHATCOUNT] read telegram dialogs error: {e}")
            return
        groups.sort(key=lambda g: g["name"].lower())
        if groups == self._last_tg_groups:
            return
        try:
            now = datetime.now(timezone.utc).isoformat()
            await self._db(lambda: self.db.from_(SETTINGS_TABLE)
                           .update({"tg_groups": groups, "tg_groups_updated_at": now}).eq("id", 1).execute())
            self._last_tg_groups = groups
            logger.info(f"[CHATCOUNT] อัปเดตรายชื่อกลุ่ม Telegram {len(groups)} กลุ่ม")
        except Exception as e:
            logger.error(f"[CHATCOUNT] publish telegram groups error: {e} (รัน chatcount_v2.sql แล้วหรือยัง?)")

    # ---------------- ตั้งเวลา ----------------
    async def _schedule_tick(self) -> None:
        now = datetime.now(TH_TZ)
        today = now.date().isoformat()
        res = await self._db(lambda: self.db.from_(SCHEDULES_TABLE).select("*").eq("active", True).execute())
        for s in res.data or []:
            sid = int(s["id"])
            if (sid, today) in self._fired:
                continue
            try:
                hh, mm = map(int, str(s.get("time") or "").split(":")[:2])
            except ValueError:
                continue
            # ส่งทุกวันเสมอ (ไม่ใช้ช่อง days แล้ว)
            due = now.replace(hour=hh, minute=mm, second=0, microsecond=0)
            delta = (now - due).total_seconds()
            if not (0 <= delta < FIRE_WINDOW):
                continue
            self._fired.add((sid, today))
            row = self.round_from_schedule(s, today, "auto")
            try:
                ins = await self._db(lambda: self.db.from_(ROUNDS_TABLE).insert(row).execute())
                logger.info(f"[CHATCOUNT] เริ่มรอบ '{row['label']}' ({today}) id={(ins.data or [{}])[0].get('id')}")
            except Exception as e:
                # ชน unique index = มีรอบของวันนี้แล้ว (เช่น บอทรีสตาร์ท) → ข้าม
                if "duplicate" not in str(e).lower() and "23505" not in str(e):
                    logger.error(f"[CHATCOUNT] create round error: {e}")

    def round_from_schedule(self, s: dict, round_date: str, trigger: str) -> dict:
        all_webs = web_name_list(self.webs)
        return {
            "schedule_id": int(s["id"]),
            "trigger": trigger,
            "label": s.get("name") or f"รอบ {s.get('time')}",
            "round_date": round_date,
            "round_time": s.get("time"),
            "channel_ids": s.get("channel_ids") or [],
            "expected_webs": all_webs,              # ถามทุกเว็บเสมอ (ใช้รายชื่อเว็บล่าสุดตอนเริ่มรอบ)
            "telegram_chat_id": s.get("telegram_chat_id"),
            "ask_template": s.get("ask_template"),
            "report_template": s.get("report_template"),
            "line_template": s.get("line_template"),
            "missing_template": s.get("missing_template"),
        }

    # ---------------- ส่งคำถามเข้าห้อง ----------------
    async def _post_round(self, r: dict) -> None:
        rid = int(r["id"])
        content = build_ask(r)
        messages, errors = [], []
        for cid in r.get("channel_ids") or []:
            ch = self._resolve_channel(cid)
            if ch is None:
                errors.append(f"{cid}: ไม่พบห้อง (บอทไม่อยู่ในห้องนี้)")
                continue
            try:
                m = await ch.send(content, allowed_mentions=discord.AllowedMentions.none())
                messages.append({"channel_id": str(cid), "channel_name": ch.name, "message_id": str(m.id)})
            except discord.Forbidden:
                errors.append(f"#{ch.name}: บอทไม่มีสิทธิ์ส่งข้อความ")
            except discord.HTTPException as e:
                errors.append(f"#{ch.name}: {e.text or e.status}")
        r.update({"posted": True, "messages": messages, "post_errors": errors})
        await self._update_round(rid, {"posted": True, "messages": messages, "post_errors": errors})
        self._rebuild_msg_map()
        logger.info(f"[CHATCOUNT] ส่งคำถามรอบ #{rid} แล้ว {len(messages)} ห้อง" + (f" · ผิดพลาด {errors}" if errors else ""))

    # ---------------- รับรีพลาย ----------------
    async def handle_message(self, message: discord.Message) -> bool:
        if message.author.bot or not message.reference or not message.reference.message_id:
            return False
        rid = self.msg_map.get(int(message.reference.message_id))
        if not rid:
            return False
        rnd = self.rounds.get(rid)
        if not rnd or rnd.get("status") != "open":
            # ข้อความคำถามถูกลบไปแล้วหลังส่งสรุป → แจ้งสั้นๆ แล้วลบตัวเองทิ้งใน 10 วินาที
            try:
                await message.reply("ℹ️ รอบนี้ส่งสรุปไปแล้วครับ", mention_author=False, delete_after=10)
            except Exception:
                pass
            return True

        found, invalid = parse_reply(message.content or "", self.web_names)
        names_hint = " ".join(f"`{w}`" for w in (rnd.get("expected_webs") or self.web_names))

        if invalid:
            # มีชื่อเว็บผิดแม้แต่ตัวเดียว → ไม่รับทั้งข้อความ กันนับครึ่งๆ กลางๆ
            wrong = ", ".join(f"`{t}`" + (f" (หมายถึง `{g}` ?)" if g else "") for t, g in invalid)
            await message.reply(
                f"❌ ชื่อเว็บไม่ถูกต้อง: {wrong}\n"
                f"ข้อความนี้ยังไม่ถูกบันทึก กรุณารีพลายใหม่ โดยพิมพ์ชื่อเว็บให้ตรงทุกตัวอักษร\n"
                f"ชื่อที่ใช้ได้: {names_hint}",
                mention_author=False,
                allowed_mentions=discord.AllowedMentions.none(),
            )
            return True

        if not found:
            if re.search(r"\d", message.content or ""):
                await message.reply(
                    f"⚠️ ไม่เจอชื่อเว็บในข้อความ กรุณาพิมพ์ชื่อเว็บนำหน้าตัวเลข เช่น `MK8 15`\n"
                    f"ชื่อที่ใช้ได้: {names_hint}",
                    mention_author=False,
                    allowed_mentions=discord.AllowedMentions.none(),
                )
            return True

        expected = set(rnd.get("expected_webs") or [])
        ch_id = str(message.channel.id)
        staff = self._staff_name(message.author.id)
        rows, accepted, skipped = [], [], []
        for web, count in found.items():
            if web not in expected:
                skipped.append(web)
                continue
            accepted.append(web)
            rows.append({
                "round_id": rid,
                "web": web,
                "count": count,
                "channel_id": ch_id,
                "channel_name": getattr(message.channel, "name", ""),
                "discord_id": str(message.author.id),
                "discord_name": getattr(message.author, "display_name", None) or message.author.name,
                "staff_name": staff,
                "message_id": str(message.id),
                "raw_text": (message.content or "")[:500],
            })

        if rows:
            try:
                await self._db(lambda: self.db.from_(ANSWERS_TABLE).insert(rows).execute())
            except Exception as e:
                logger.error(f"[CHATCOUNT] save answers error: {e}")
                await message.reply("❌ บันทึกไม่สำเร็จ ลองรีพลายใหม่อีกครั้งครับ", mention_author=False)
                return True

        lines = []
        if accepted:
            lines.append("✅ รับแล้ว: " + ", ".join(f"{w} = {found[w]}" for w in accepted))
        if skipped:
            lines.append("⚠️ ไม่ได้อยู่ในรอบนี้: " + ", ".join(skipped))

        answers = await self._fetch_answers(rid) if rows else []
        resolved = rnd.get("resolved") or {}
        clash = sorted({a["web"] for a in answers
                        if a["web"] in accepted and str(a.get("channel_id")) != ch_id and a["web"] not in resolved})
        if clash:
            lines.append("⚠️ " + ", ".join(clash) + " มีคำตอบจากห้องอื่นด้วย — รอแอดมินตรวจในเว็บก่อนส่งสรุป")

        try:
            if accepted:
                await message.add_reaction("✅")
            await message.reply("\n".join(lines), mention_author=False,
                                allowed_mentions=discord.AllowedMentions.none())
        except Exception as e:
            logger.warning(f"[CHATCOUNT] reply error: {e}")

        logger.info(f"[CHATCOUNT] รอบ #{rid} {staff or message.author} → {found}")
        if rows:
            await self.evaluate_and_send(rid)
        return True

    # ---------------- ตรวจครบ + ส่ง Telegram ----------------
    async def evaluate_and_send(self, rid: int) -> None:
        async with self._send_lock:
            res = await self._db(lambda: self.db.from_(ROUNDS_TABLE).select("*").eq("id", rid).execute())
            if not res.data:
                return
            rnd = res.data[0]
            self.rounds[rid] = rnd
            if rnd.get("status") != "open" or not rnd.get("posted"):
                return

            answers = await self._fetch_answers(rid)
            ev = evaluate_round(rnd, answers)
            force = bool(rnd.get("force_send"))
            if not ev["complete"] and not force:
                return

            text = build_report(rnd, ev, force, web_emoji_map(self.webs))
            ok, err = await self._send_telegram(rnd.get("telegram_chat_id"), text)
            if not ok:
                if rnd.get("send_error") != err:
                    await self._update_round(rid, {"send_error": err, "report_text": text})
                    logger.warning(f"[CHATCOUNT] รอบ #{rid} ส่ง Telegram ไม่ได้: {err}")
                return

            await self._update_round(rid, {
                "status": "sent",
                "report_text": text,
                "send_error": None,
                "sent_at": datetime.now(timezone.utc).isoformat(),
            })
            rnd["status"] = "sent"
            logger.info(f"[CHATCOUNT] รอบ #{rid} ส่งสรุปเข้า Telegram แล้ว ({'ส่งเท่าที่มี' if force else 'ครบ'})")

        try:
            await self._cleanup_discord(rnd)
        except Exception as e:
            logger.error(f"[CHATCOUNT] cleanup discord error: {e}")

    async def _cleanup_discord(self, rnd: dict) -> None:
        """ลบข้อความคำถาม + รีพลายของพนักงาน + ข้อความตอบกลับของบอท ของรอบนี้ออกจากทุกห้อง"""
        me = self.bot.user
        total = 0
        for msg in rnd.get("messages") or []:
            ch = self._resolve_channel(msg.get("channel_id"))
            if not ch:
                continue
            try:
                qid = int(msg["message_id"])
            except (KeyError, TypeError, ValueError):
                continue
            ids = [qid]
            chain: set[int] = {qid}
            try:
                # ไล่ข้อความหลังคำถาม เก็บทุกข้อความที่ "รีพลายต่อกันเป็นสาย" จากคำถาม
                #   คำถาม ← รีพลายพนักงาน ← บอทตอบ (✅/❌) ← พนักงานตอบบอทต่อ ...
                # + ข้อความตอบกลับของบอทที่ "ข้อความต้นฉบับถูกลบไปแล้ว" (เช่น พนักงานลบข้อความที่พิมพ์ผิดเอง)
                #   ดูจากหัวข้อความ เพื่อไม่ไปลบข้อความแจ้งเตือนอื่นของบอทในห้องเดียวกัน
                async for m in ch.history(after=discord.Object(id=qid), limit=None, oldest_first=True):
                    ref = m.reference.message_id if m.reference else None
                    if ref is None:
                        continue
                    if ref in chain:
                        ids.append(m.id)
                        chain.add(m.id)
                    elif me is not None and m.author.id == me.id and (m.content or "").startswith(_BOT_REPLY_PREFIXES):
                        ids.append(m.id)
                        chain.add(m.id)
            except (discord.Forbidden, discord.HTTPException) as e:
                logger.warning(f"[CHATCOUNT] อ่านประวัติห้อง #{getattr(ch, 'name', '')} ไม่ได้: {e}")
            total += await self._delete_many(ch, ids)
        logger.info(f"[CHATCOUNT] รอบ #{rnd.get('id')} ลบข้อความใน Discord แล้ว {total} ข้อความ")

    async def _delete_many(self, ch, ids: list[int]) -> int:
        deleted = 0
        for i in range(0, len(ids), 100):                    # bulk delete ได้ครั้งละไม่เกิน 100
            chunk = ids[i:i + 100]
            try:
                await ch.delete_messages([discord.Object(id=x) for x in chunk])
                deleted += len(chunk)
                continue
            except discord.Forbidden:
                logger.warning(f"[CHATCOUNT] บอทไม่มีสิทธิ์ Manage Messages ใน #{getattr(ch, 'name', '')} → ลบได้เฉพาะข้อความของบอท")
            except discord.HTTPException as e:
                logger.warning(f"[CHATCOUNT] bulk delete ไม่สำเร็จ ({e}) → ลบทีละข้อความ")
            for x in chunk:                                   # สำรอง: ลบทีละข้อความ (ข้อความของบอทเองลบได้เสมอ)
                try:
                    await ch.get_partial_message(x).delete()
                    deleted += 1
                except Exception:
                    pass
        return deleted

    async def _send_telegram(self, chat_id, text: str) -> tuple[bool, str]:
        client = self.get_tg_client()
        if client is None:
            return False, "Telegram ยังไม่เชื่อมต่อ (บอทจะลองส่งใหม่เอง)"
        target = self.norm_chat_id(chat_id)
        if not target:
            return False, "ยังไม่ได้เลือกกลุ่ม Telegram ปลายทาง"

        if self._tg_cache_owner is not client:
            self._tg_cache, self._tg_cache_owner = {}, client
        entity = self._tg_cache.get(target)
        try:
            if entity is None:
                async for d in client.iter_dialogs():
                    self._tg_cache[self.norm_chat_id(d.id)] = d.entity
                entity = self._tg_cache.get(target)
            if entity is None:
                return False, f"บัญชี Telegram นี้ไม่ได้อยู่ในกลุ่ม {chat_id}"
            await client.send_message(entity, text, parse_mode=None, link_preview=False)
            return True, ""
        except Exception as e:
            self._tg_cache.pop(target, None)
            return False, f"ส่ง Telegram ไม่สำเร็จ: {type(e).__name__}: {e}"[:300]
