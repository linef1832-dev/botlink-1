export interface ActivityEvent {
  id: string;
  timestamp: Date;
  employeeName: string;
  telegramId: string;
  discordId: string | null;
  activity: string;
  isReturn: boolean;
  groupName: string;
  discordChannelName: string | null;
  sent: boolean;
}

const MAX_EVENTS = 200;
const log: ActivityEvent[] = [];

export function addEvent(event: Omit<ActivityEvent, "id" | "timestamp">): ActivityEvent {
  const entry: ActivityEvent = {
    id: Date.now().toString(36) + Math.random().toString(36).slice(2, 6),
    timestamp: new Date(),
    ...event,
  };
  log.unshift(entry);
  if (log.length > MAX_EVENTS) log.length = MAX_EVENTS;
  return entry;
}

export function getRecentEvents(limit = 50): ActivityEvent[] {
  return log.slice(0, limit);
}

export function getStats() {
  const today = new Date();
  today.setHours(0, 0, 0, 0);
  const todayEvents = log.filter((e) => e.timestamp >= today);
  return {
    totalToday: todayEvents.length,
    sentToday: todayEvents.filter((e) => e.sent).length,
    byActivity: todayEvents.reduce<Record<string, number>>((acc, e) => {
      acc[e.activity] = (acc[e.activity] ?? 0) + 1;
      return acc;
    }, {}),
  };
}
