"""ชื่อเล่น Discord ของพนักงาน = [สีเว็บวันนี้][ชื่อเดิม] [อีโมจิพัก]

    🟣👑 LAYLA        ← วันนี้ทำเว็บ PG688 (สีม่วง)
    🟣👑 LAYLA 🚽     ← กำลังไปห้องน้ำ
    🟣🟠👑 KAI        ← OD: งานหลัก PG688 + งานรอง F168 (แผนกอื่นใช้สีงานหลักสีเดียว)

- สีเว็บอยู่ "หน้าสุด" → Discord เรียงรายชื่อตามตัวอักษร คนเว็บเดียวกันเลยอยู่ติดกัน
- อีโมจิพักอยู่ "ท้ายสุด" → ไปพักแล้วไม่หลุดออกจากกลุ่ม ตำแหน่งในลิสต์ไม่ขยับ
- "ชื่อเดิม" คือทุกอย่างที่เหลือ (รวม 👑 🍀 ที่ตั้งกันเอง) บอทไม่แตะ

ไฟล์นี้มีแต่ฟังก์ชันล้วน (ไม่ยุ่งกับ Discord/DB) — ทดสอบแยกได้
"""
from __future__ import annotations

import json
from datetime import datetime, timedelta

NICK_MAX = 32                      # Discord จำกัดชื่อเล่นไม่เกิน 32 ตัวอักษร (นับแบบ UTF-16)
DUTY_SHIFTS = ("กะเช้า", "กะกลาง", "กะดึก")
DAY_SHIFTS = ("กะเช้า", "กะกลาง")  # กะกลางวัน — ใช้วันที่ตามปฏิทิน (เหมือนเว็บ K36)
MULTI_WEB_DEPTS = {"OD"}            # แผนกที่แสดงสีงานรองต่อจากงานหลัก (แผนกอื่นใช้สีงานหลักอย่างเดียว)

# สีประจำเว็บ (ค่าเริ่มต้นเดียวกับหน้าเว็บ K36) — ถ้าตั้งไว้ในเว็บ K36 หน้านับแชท จะใช้ค่าจากเว็บแทน
DEFAULT_WEB_EMOJI = {
    "Jun88": "🔵", "MK8": "⚫️", "PG688": "🟣", "F168": "🟠", "JL69": "🟡",
    "NM9": "🔴", "VV72": "🔘", "TH26": "🟤", "K188": "🩷", "BT678": "⚪️",
}

# อีโมจิที่ Discord บน Windows แสดงในชื่อไม่ได้ (ขึ้นเป็นกล่อง □) → ใช้ตัวที่หน้าตาใกล้กันแทน เฉพาะในชื่อ
# (🩷 เป็นอีโมจิใหม่ Unicode 15 — ในข้อความ Telegram ยังใช้ 🩷 ตามที่ตั้งในเว็บได้ปกติ)
NICK_EMOJI_FALLBACK = {"🩷": "💗", "🩵": "💙", "🩶": "🤍"}

VS16 = "\ufe0f"                    # ตัวแปรรูปอีโมจิ (⚫️ = ⚫ + VS16) — พิมพ์มาบ้างไม่มาบ้าง ต้องรับทั้งคู่


def u16len(s: str) -> int:
    return len(s.encode("utf-16-le")) // 2


def _variants(emojis) -> list[tuple[str, str]]:
    """[(แบบที่อาจเจอในชื่อ, แบบมาตรฐาน)] เรียงยาวก่อน — ⚫️ ต้องเช็คก่อน ⚫"""
    out = {}
    for e in emojis:
        e = str(e or "").strip()
        if not e:
            continue
        for v in (e, e.replace(VS16, "")):
            if v:
                out.setdefault(v, e)
    return sorted(out.items(), key=lambda kv: len(kv[0]), reverse=True)


def parse_nick(nick: str | None, web_emojis, break_emojis) -> tuple[str, str, str]:
    """แยกชื่อเล่นเป็น (สีเว็บทั้งหมดที่อยู่หน้าชื่อ, ชื่อเดิม, อีโมจิพัก)

    รับได้ทั้งแบบใหม่ (พักอยู่ท้าย) และแบบเก่า (พักอยู่หน้า เช่น "🚽👑 LAYLA") แล้วจัดใหม่ให้"""
    s = nick or ""
    web_v, brk_v = _variants(web_emojis), _variants(break_emojis)
    web = brk = ""
    changed = True
    while s and changed:                                   # ตัดหัว: สีเว็บ / อีโมจิพักแบบเก่า
        changed = False
        if s.startswith(VS16):
            s, changed = s[len(VS16):], True
            continue
        for v, canon in web_v:
            if s.startswith(v):
                web += canon                                 # เก็บทุกสี (OD มีได้ 2 สี)
                s, changed = s[len(v):], True
                break
        else:
            for v, canon in brk_v:
                if s.startswith(v):
                    brk = brk or canon
                    s, changed = s[len(v):], True
                    break
    changed = True
    while s and changed:                                   # ตัดท้าย: อีโมจิพัก
        changed = False
        t = s.rstrip()
        for v, canon in brk_v:
            if t.endswith(v):
                brk = brk or canon
                s, changed = t[: -len(v)].rstrip(), True
                break
    return web, s, brk


def build_nick(base: str, web: str = "", brk: str = "") -> str:
    """ประกอบชื่อ (ตัดท้ายชื่อเดิมถ้ายาวเกินที่ Discord รับ — สีเว็บ/อีโมจิพักไม่โดนตัด)"""
    suffix = f" {brk}" if brk else ""
    base = base or ""
    while base and u16len(web + base + suffix) > NICK_MAX:
        base = base[:-1]
    return f"{web}{base}{suffix}"


def web_emoji_map(webs) -> dict[str, str]:
    """{ชื่อเว็บตัวเล็ก: สี} — ค่าจากเว็บ K36 (chatcount_settings.webs) ทับค่าเริ่มต้น"""
    out = {k.lower(): v for k, v in DEFAULT_WEB_EMOJI.items()}
    for w in webs or []:
        if isinstance(w, dict) and w.get("name") and str(w.get("emoji") or "").strip():
            out[str(w["name"]).strip().lower()] = str(w["emoji"]).strip()
    return {k: NICK_EMOJI_FALLBACK.get(v, v) for k, v in out.items()}


def all_web_emojis(web_map: dict[str, str]) -> set[str]:
    """สีเว็บทั้งหมดที่อาจอยู่ในชื่อ (รวมตัวเดิมก่อนแทน เช่น 🩷 — เพื่อให้ลบออกจากชื่อเก่าได้)"""
    return set(web_map.values()) | set(DEFAULT_WEB_EMOJI.values()) | set(NICK_EMOJI_FALLBACK)


def work_date(shift: str, now: datetime) -> str:
    """วันที่ของตารางหน้าที่ที่ "กำลังทำอยู่ตอนนี้" (กฎเดียวกับเว็บ K36: กะดึกก่อน 08:00 = ตารางของเมื่อวาน)"""
    d = now
    if shift not in DAY_SHIFTS and now.hour < 8:
        d = now - timedelta(days=1)
    return d.strftime("%Y-%m-%d")


def duty_dept(dept: str) -> str:
    d = (dept or "").strip() or "AM"
    return "AMQL" if d == "TRAINER" else d


def roster_keys(emp: dict, now: datetime) -> list[str]:
    """key ในตาราง settings ของตารางหน้าที่ที่คนนี้อาจอยู่ — กะของตัวเองก่อน แล้วค่อยกะอื่น
    (ค้นทุกกะ เพราะมีสลับกะ/ย้ายกะชั่วคราว — คนที่ allowed_shift เป็นกะกลางแต่วันนี้ไปอยู่กะดึกก็ต้องเจอ)"""
    dept = duty_dept(emp.get("department", ""))
    shift = (emp.get("allowed_shift") or "").strip()
    shifts = ([shift] if shift in DUTY_SHIFTS else []) + [s for s in DUTY_SHIFTS if s != shift]
    return [f"duty_roster_{dept}_{work_date(s, now)}_{s}" for s in shifts]


def own_roster_keys(emp: dict, now: datetime) -> list[str]:
    """key ที่ "ต้องมี" ก่อนจะสรุปได้ว่าวันนี้คนนี้ไม่ได้ทำเว็บไหน (กะตัวเอง / ไม่รู้กะ = ทุกกะ)"""
    keys = roster_keys(emp, now)
    return keys[:1] if (emp.get("allowed_shift") or "").strip() in DUTY_SHIFTS else keys


def find_teams(roster: dict, username: str, with_secondary: bool = False) -> list[str]:
    """หาว่าคนนี้อยู่เว็บไหน → [งานหลัก] หรือ [งานหลัก, งานรอง] (ถ้า with_secondary และงานรองเป็นคนละเว็บ)
    ไม่เจอคืน []"""
    want = (username or "").strip().lower()
    for team, members in (roster or {}).items():
        for u in members or []:
            name = str((u or {}).get("username") or "")
            if name and "ขาดคน" not in name and name.strip().lower() == want:
                teams = [str(team)]
                sec = str((u or {}).get("secondary_team") or "").strip()
                if with_secondary and sec and sec.lower() != str(team).lower():
                    teams.append(sec)
                return teams
    return []


def resolve_today_webs(employees: dict, rows: dict[str, str], now: datetime) -> dict[str, list[str]]:
    """{discord_id: [เว็บงานหลัก, (เว็บงานรอง — เฉพาะ OD)] หรือ [] (มีตารางแล้วแต่ไม่มีชื่อ = หยุด/ลา)}

    คนที่ "ยังไม่มีตารางของวันนี้" จะไม่อยู่ในผลลัพธ์ → บอทไม่แตะสีเดิม (กันสีหายตอนแอดมินยังไม่จัดหน้าที่)
    employees = EMPLOYEES จาก main.py / rows = {key: value(JSON)} จากตาราง settings"""
    parsed: dict[str, dict | None] = {}
    for k, v in (rows or {}).items():
        try:
            parsed[k] = json.loads(v) if isinstance(v, str) else (v or {})
        except (TypeError, ValueError):
            parsed[k] = None
    out: dict[str, list[str]] = {}
    for emp in (employees or {}).values():
        did = str(emp.get("discord_id") or "")
        if not did:
            continue
        keys = roster_keys(emp, now)
        with_sec = duty_dept(emp.get("department", "")) in MULTI_WEB_DEPTS
        rosters = [parsed[k] for k in keys if isinstance(parsed.get(k), dict)]
        teams = next((t for r in rosters if (t := find_teams(r, emp.get("name", ""), with_sec))), [])
        if teams:
            out[did] = teams
        elif all(isinstance(parsed.get(k), dict) for k in own_roster_keys(emp, now)):
            out[did] = []                       # ตารางกะตัวเองมีแล้ว แต่ไม่มีชื่อ (และไม่อยู่กะอื่น) → วันนี้ไม่ได้ทำเว็บไหน
    return out
