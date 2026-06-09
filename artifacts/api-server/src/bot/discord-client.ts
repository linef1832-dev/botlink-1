import { Client, GatewayIntentBits, ChannelType, TextChannel } from "discord.js";
import { logger } from "../lib/logger";

let client: Client | null = null;
let ready = false;

export function getDiscordClient(): Client | null {
  return client;
}

export function isDiscordReady(): boolean {
  return ready;
}

export async function startDiscord(token: string): Promise<void> {
  client = new Client({
    intents: [
      GatewayIntentBits.Guilds,
      GatewayIntentBits.GuildMembers,
      GatewayIntentBits.GuildVoiceStates,
    ],
  });

  client.once("ready", () => {
    ready = true;
    logger.info({ tag: "discord", user: client?.user?.tag }, "Discord bot ready");
  });

  client.on("error", (err) => {
    logger.error({ err, tag: "discord" }, "Discord client error");
  });

  await client.login(token);

  await new Promise<void>((resolve, reject) => {
    const timeout = setTimeout(() => reject(new Error("Discord login timeout")), 15000);
    client!.once("ready", () => {
      clearTimeout(timeout);
      resolve();
    });
  });
}

export async function findTextChannelForMember(discordUserId: string): Promise<TextChannel | null> {
  if (!client || !ready) return null;

  for (const guild of client.guilds.cache.values()) {
    try {
      const member = await guild.members.fetch(discordUserId).catch(() => null);
      if (!member) continue;

      const voiceChannel = member.voice.channel;
      if (!voiceChannel) {
        logger.info({ discordUserId, tag: "discord" }, "Member not in voice channel");
        return null;
      }

      logger.info(
        { discordUserId, voiceChannel: voiceChannel.name, tag: "discord" },
        "Found member in voice channel"
      );

      const category = voiceChannel.parent;
      if (category) {
        const textCh = category.children.cache.find(
          (ch) => ch.type === ChannelType.GuildText
        ) as TextChannel | undefined;
        if (textCh) return textCh;
      }

      const guildText = guild.channels.cache.find(
        (ch) =>
          ch.type === ChannelType.GuildText &&
          ch.name.toLowerCase().includes(voiceChannel.name.toLowerCase().slice(0, 6))
      ) as TextChannel | undefined;
      if (guildText) return guildText;

    } catch (err) {
      logger.error({ err, guild: guild.name, tag: "discord" }, "Error fetching member");
    }
  }

  return null;
}

export async function sendToChannel(channelId: string, message: string): Promise<boolean> {
  if (!client || !ready) return false;
  try {
    const channel = await client.channels.fetch(channelId);
    if (!channel || channel.type !== ChannelType.GuildText) return false;
    await (channel as TextChannel).send(message);
    return true;
  } catch (err) {
    logger.error({ err, channelId, tag: "discord" }, "Failed to send to fallback channel");
    return false;
  }
}

export async function sendActivityNotification(
  discordUserId: string,
  employeeName: string,
  activity: string,
  isReturn: boolean,
  groupName: string,
  fallbackChannelId?: string
): Promise<{ sent: boolean; channelName: string | null }> {
  if (!client || !ready) return { sent: false, channelName: null };

  const emoji = isReturn ? "✅" : getActivityEmoji(activity);
  const actionText = isReturn
    ? `**${employeeName}** กลับที่นั่งแล้ว`
    : `**${employeeName}** ไป${activity}`;

  const now = new Date().toLocaleTimeString("th-TH", { hour: "2-digit", minute: "2-digit" });
  const message = `${emoji} ${actionText}\n> 🕐 ${now} · 📌 ${groupName}`;

  const textChannel = await findTextChannelForMember(discordUserId);

  if (textChannel) {
    try {
      await textChannel.send(message);
      return { sent: true, channelName: textChannel.name };
    } catch (err) {
      logger.error({ err, tag: "discord" }, "Failed to send to voice-linked text channel");
    }
  }

  if (fallbackChannelId) {
    const sent = await sendToChannel(fallbackChannelId, message);
    return { sent, channelName: sent ? "fallback" : null };
  }

  logger.warn({ discordUserId, employeeName, tag: "discord" }, "No channel found for member");
  return { sent: false, channelName: null };
}

function getActivityEmoji(activity: string): string {
  if (activity.includes("กินข้าว") || activity.includes("ทาน")) return "🍚";
  if (activity.includes("ปวดหนัก")) return "🚽";
  if (activity.includes("ปวดน้อย")) return "🚾";
  if (activity.includes("พัก")) return "☕";
  return "🚶";
}
