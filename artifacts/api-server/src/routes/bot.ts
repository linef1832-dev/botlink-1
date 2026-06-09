import { Router } from "express";
import {
  getRecentEvents,
  getStats,
  getTelegramStatus,
  submitPhoneCode,
  submitPassword,
  getSessionString,
  isDiscordReady,
} from "../bot/index";

const router = Router();

router.get("/bot/status", (_req, res) => {
  const telegramStatus = getTelegramStatus();
  res.json({
    telegram: telegramStatus,
    discord: { ready: isDiscordReady() },
    session: telegramStatus.connected
      ? "connected"
      : process.env["TELEGRAM_SESSION"]
      ? "has_session_not_started"
      : "no_session",
  });
});

router.post("/bot/auth/code", (req, res) => {
  const { code } = req.body as { code?: string };
  if (!code) {
    res.status(400).json({ error: "code required" });
    return;
  }
  submitPhoneCode(code);
  res.json({ ok: true, message: "Code submitted. Check logs for session string." });
});

router.post("/bot/auth/password", (req, res) => {
  const { password } = req.body as { password?: string };
  if (!password) {
    res.status(400).json({ error: "password required" });
    return;
  }
  submitPassword(password);
  res.json({ ok: true, message: "Password submitted." });
});

router.get("/bot/session", (_req, res) => {
  const session = getSessionString();
  if (!session) {
    res.status(404).json({ error: "No session available" });
    return;
  }
  res.json({
    session,
    message: "Save this as TELEGRAM_SESSION secret to skip auth next time",
  });
});

router.get("/bot/activity", (_req, res) => {
  const limit = Math.min(parseInt(String((_req.query as Record<string, string>)["limit"] ?? "50")), 200);
  res.json({
    events: getRecentEvents(limit),
    stats: getStats(),
  });
});

export default router;
