# botlink

บอทเชื่อม Telegram → Discord สำหรับระบบเช็คอินพัก + นับแชท (K36)

- ดักข้อความเช็คอินจากกลุ่ม Telegram → แจ้งในห้องเสียง Discord, ย้ายห้องตอนกินข้าว, บันทึกเวลาพักลง Supabase
- นับแชท: ส่งคำถามเข้าห้อง Discord ตามเวลา → รับรีพลาย → ส่งสรุปเข้ากลุ่ม Telegram

## โครงสร้าง

| ไฟล์ | หน้าที่ |
|---|---|
| `bot/main.py` | ตัวหลัก: Telegram + Discord + บันทึกพัก + คำสั่ง `/status`, `/clear_break_messages` |
| `bot/chat_count.py` | ระบบนับแชท |
| `bot/auth.py` | รันครั้งเดียวเพื่อสร้าง `TELEGRAM_SESSION` (`python bot/auth.py`) |
| `Dockerfile` + `railway.json` | deploy บน Railway |

## Environment variables (ตั้งใน Railway → Variables)

| ตัวแปร | จำเป็น | ความหมาย |
|---|---|---|
| `DISCORD_BOT_TOKEN` | ✅ | token บอท Discord |
| `TELEGRAM_API_ID` / `TELEGRAM_API_HASH` / `TELEGRAM_PHONE` | ✅ | บัญชี Telegram |
| `TELEGRAM_SESSION` | ✅ | ได้จาก `python bot/auth.py` |
| `SUPABASE_URL` / `SUPABASE_KEY` | ✅ | ฐานข้อมูล K36 |
| `DISCORD_STATUS_CHANNEL_ID` | | ห้องแจ้งสถานะบอท + ห้องที่ใช้ `/status` ได้ |
| `TELEGRAM_TZ_OFFSET` | | โซนเวลาในข้อความ Telegram (ค่าเริ่มต้น `8` = เวลาจีน, ตั้ง `7` ถ้าเป็นเวลาไทย) |
| `BREAK_MSG_DELETE_AFTER` | | ลบข้อความพักหลังกลับที่นั่งกี่วินาที (ค่าเริ่มต้น `60`, `-1` = ไม่ลบ) |
| `CLEAR_ALLOWED_ROLES` | | role ที่ใช้ `/clear_break_messages` ได้ คั่นด้วย `,` (ค่าเริ่มต้น `PTT`) |
| `LOG_UNKNOWN_GROUPS` | | `1` = พิมพ์ chat_id ของกลุ่มที่ยังไม่ได้ตั้งค่า (ใช้ตอนหา chat_id กลุ่มใหม่) |

## รันในเครื่อง

```
pip install -r requirements.txt
python bot/main.py
```
