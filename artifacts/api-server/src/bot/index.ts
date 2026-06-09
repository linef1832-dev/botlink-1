import { logger } from "../lib/logger";
import { byTelegramId } from "./employees";
import { addEvent, getRecentEvents, getStats } from "./activity-log";
import {
  startTelegram,
  getTelegramStatus,
  submitPhoneCode,
  submitPassword,
  getSessionString,
} from "./telegram-client";
import {
  startDiscord,
  isDiscordReady,
  sendActivityNotification,
} from "./discord-client";
import type { ParsedActivity } from "./message-parser";

export { getRecentEvents, getStats, getTelegramStatus, submitPhoneCode, submitPassword, getSessionString, isDiscordReady };

let botStarted = false;

export async function startBot(): Promise<void> {
  if (botStarted) return;
  botStarted = true;

  const apiId = parseInt(process.env["TELEGRAM_API_ID"] ?? "0", 10);
  const apiHash = process.env["TELEGRAM_API_HASH"] ?? "";
  const phone = process.env["TELEGRAM_PHONE"] ?? "";
  const sessionString = process.env["TELEGRAM_SESSION"] ?? "";
  const discordToken = process.env["DISCORD_BOT_TOKEN"] ?? "";
  const fallbackChannelId = process.env["DISCORD_CHANNEL_ID"] ?? "";

  if (!apiId || !apiHash || !phone) {
    logger.warn({ tag: "bot" }, "Missing Telegram credentials. Bot not started. Set TELEGRAM_API_ID, TELEGRAM_API_HASH, TELEGRAM_PHONE secrets.");
    return;
  }

  if (!discordToken) {
    logger.warn({ tag: "bot" }, "Missing DISCORD_BOT_TOKEN. Discord notifications disabled.");
  }

  if (discordToken) {
    try {
      await startDiscord(discordToken);
      logger.info({ tag: "bot" }, "Discord bot started");
    } catch (err) {
      logger.error({ err, tag: "bot" }, "Failed to start Discord bot");
    }
  }

  try {
    await startTelegram(apiId, apiHash, phone, sessionString, async (parsed: ParsedActivity) => {
      const employee = byTelegramId.get(parsed.telegramUserId);

      const event = addEvent({
        employeeName: employee?.name ?? parsed.employeeName,
        telegramId: parsed.telegramUserId,
        discordId: employee?.discordId ?? null,
        activity: parsed.activity,
        isReturn: parsed.isReturn,
        groupName: parsed.groupName,
        discordChannelName: null,
        sent: false,
      });

      if (!employee) {
        logger.warn(
          { telegramId: parsed.telegramUserId, name: parsed.employeeName, tag: "bot" },
          "Employee not found in mapping"
        );
        return;
      }

      if (!isDiscordReady()) {
        logger.warn({ tag: "bot" }, "Discord not ready, skipping notification");
        return;
      }

      const result = await sendActivityNotification(
        employee.discordId,
        employee.name,
        parsed.activity,
        parsed.isReturn,
        parsed.groupName,
        fallbackChannelId || undefined
      );

      event.sent = result.sent;
      event.discordChannelName = result.channelName;

      if (result.sent) {
        logger.info(
          { employee: employee.name, activity: parsed.activity, channel: result.channelName, tag: "bot" },
          "Notification sent to Discord"
        );
      } else {
        logger.warn(
          { employee: employee.name, discordId: employee.discordId, tag: "bot" },
          "Failed to send Discord notification"
        );
      }
    });
  } catch (err) {
    logger.error({ err, tag: "bot" }, "Failed to start Telegram client");
  }
}
