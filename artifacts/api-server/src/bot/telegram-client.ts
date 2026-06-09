import { TelegramClient } from "telegram";
import { StringSession } from "telegram/sessions/index.js";
import { NewMessage, NewMessageEvent } from "telegram/events/index.js";
import { logger } from "../lib/logger";
import { parseActivityMessage } from "./message-parser";

export type ActivityHandler = (parsed: ReturnType<typeof parseActivityMessage> & object) => void;

let tgClient: TelegramClient | null = null;
let connected = false;

let pendingCodeResolve: ((code: string) => void) | null = null;
let pendingPasswordResolve: ((pw: string) => void) | null = null;
let authStatus: "idle" | "awaiting_code" | "awaiting_password" | "connected" | "error" = "idle";
let authError: string | null = null;

const TARGET_GROUPS = [
  "Jun88-กลุ่มเช็คอิน打卡群",
  "Jun88-OL กลุ่มเช็คอิน 打卡群",
];

export function getTelegramStatus() {
  return { connected, authStatus, authError };
}

export function submitPhoneCode(code: string) {
  if (pendingCodeResolve) {
    pendingCodeResolve(code);
    pendingCodeResolve = null;
    authStatus = "connected";
  }
}

export function submitPassword(password: string) {
  if (pendingPasswordResolve) {
    pendingPasswordResolve(password);
    pendingPasswordResolve = null;
  }
}

export async function startTelegram(
  apiId: number,
  apiHash: string,
  phone: string,
  sessionString: string,
  onActivity: ActivityHandler
): Promise<void> {
  const session = new StringSession(sessionString);

  tgClient = new TelegramClient(session, apiId, apiHash, {
    connectionRetries: 5,
  });

  authStatus = "idle";

  await tgClient.start({
    phoneNumber: async () => phone,
    phoneCode: async () => {
      authStatus = "awaiting_code";
      logger.info({ tag: "telegram" }, "Waiting for phone code...");
      return new Promise<string>((resolve) => {
        pendingCodeResolve = resolve;
      });
    },
    password: async () => {
      authStatus = "awaiting_password";
      logger.info({ tag: "telegram" }, "Waiting for 2FA password...");
      return new Promise<string>((resolve) => {
        pendingPasswordResolve = resolve;
      });
    },
    onError: (err: Error) => {
      authError = err.message;
      authStatus = "error";
      logger.error({ err, tag: "telegram" }, "Telegram auth error");
    },
  });

  connected = true;
  authStatus = "connected";

  const savedSession = (tgClient.session as StringSession).save();
  if (savedSession && savedSession !== sessionString) {
    logger.info({ tag: "telegram" }, "New session string (save as TELEGRAM_SESSION secret):");
    logger.info({ session: savedSession, tag: "telegram" }, "SESSION_STRING");
  }

  const me = await tgClient.getMe();
  logger.info({ user: (me as { username?: string }).username ?? "unknown", tag: "telegram" }, "Telegram connected");

  tgClient.addEventHandler(async (event: NewMessageEvent) => {
    const msg = event.message;
    if (!msg || !msg.text) return;

    try {
      const chat = await msg.getChat();
      const chatTitle = (chat as { title?: string }).title ?? "";
      const matchedGroup = TARGET_GROUPS.find((g) =>
        chatTitle.includes(g.replace(/\s+/g, ""))
        || g.replace(/\s+/g, "").includes(chatTitle.replace(/\s+/g, ""))
        || chatTitle === g
      );

      if (!matchedGroup && !TARGET_GROUPS.some((g) => chatTitle.includes("Jun88"))) return;

      const parsed = parseActivityMessage(msg.text, chatTitle);
      if (!parsed) return;

      logger.info({ activity: parsed.activity, user: parsed.telegramUserId, tag: "telegram" }, "Activity detected");
      onActivity(parsed as Parameters<ActivityHandler>[0]);
    } catch (err) {
      logger.error({ err, tag: "telegram" }, "Error handling message");
    }
  }, new NewMessage({}));

  logger.info({ groups: TARGET_GROUPS, tag: "telegram" }, "Listening to Telegram groups");
}

export function getSessionString(): string | null {
  if (!tgClient) return null;
  return (tgClient.session as StringSession).save() ?? null;
}
