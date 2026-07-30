import { Router } from "express";
import { getRecentEvents, getStats } from "../bot/activity-log";

const router = Router();

router.get("/bot/status", (_req, res) => {
  res.json({
    telegram: { connected: false, note: "Managed by Python bot (bot/main.py)" },
    discord: { ready: false, note: "Managed by Python bot (bot/main.py)" },
  });
});

router.get("/bot/activity", (req, res) => {
  const limit = Math.min(
    parseInt(String((req.query as Record<string, string>)["limit"] ?? "50")),
    200
  );
  res.json({
    events: getRecentEvents(limit),
    stats: getStats(),
  });
});

export default router;
