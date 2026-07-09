// ไฟล์นี้ไม่ได้ใช้งานแล้ว
// ข้อมูลพนักงานถูกดึงจาก Supabase K36 ตาราง users โดยตรงใน bot/main.py
// ดู load_employees_from_supabase() ใน bot/main.py

export interface Employee {
  name: string;
  telegramId: string;
  discordId: string;
  allowedShift?: string;
  department?: string;
}

// ไม่มีข้อมูล hardcode อีกต่อไป — ดึงจาก Supabase K36 แทน
export const employees: Employee[] = [];
