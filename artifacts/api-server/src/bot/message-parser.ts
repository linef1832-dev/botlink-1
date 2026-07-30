export interface ParsedActivity {
  telegramUserId: string;
  employeeName: string;
  activity: string;
  isReturn: boolean;
  timestamp: string;
  duration?: string;
  groupName: string;
}

const RETURN_KEYWORDS = ["กลับที่นั่ง", "กลับที่นัง", "回座"];
const ACTIVITY_PATTERN = /ลงทะเบียนสำหรับ\s+(.+?)(?:\s+สำเร็จ)?(?:\s*[:：].*)?$/m;
const TELEGRAM_ID_PATTERN = /รหัสผู้ใช้[：:]\s*(\d+)/;
const TIMESTAMP_PATTERN = /(\d{2}\/\d{2}\s+\d{2}:\d{2}:\d{2})/;
const DURATION_PATTERN = /เวลากิจกรรมนี้[：:]\s*([\d:]+)/;

export function parseActivityMessage(
  text: string,
  groupName: string
): ParsedActivity | null {
  if (!text) return null;

  const telegramIdMatch = TELEGRAM_ID_PATTERN.exec(text);
  if (!telegramIdMatch) return null;

  const activityMatch = ACTIVITY_PATTERN.exec(text);
  if (!activityMatch) return null;

  const activity = activityMatch[1].trim();
  const isReturn = RETURN_KEYWORDS.some((kw) => activity.includes(kw));

  const timestampMatch = TIMESTAMP_PATTERN.exec(text);
  const timestamp = timestampMatch ? timestampMatch[1] : new Date().toLocaleString("th-TH");

  const durationMatch = DURATION_PATTERN.exec(text);
  const duration = durationMatch ? durationMatch[1] : undefined;

  const nameMatch = /ผู้ใช้[：:]\s*(.+?)(?:\s*\n|\s*$)/.exec(text);
  const employeeName = nameMatch ? nameMatch[1].trim() : "Unknown";

  return {
    telegramUserId: telegramIdMatch[1],
    employeeName,
    activity: isReturn ? "กลับที่นั่ง" : activity,
    isReturn,
    timestamp,
    duration,
    groupName,
  };
}
