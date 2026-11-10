import { api } from "./api";
import { goTo } from "./nav";
import type { NotificationChat, NotificationItem } from "./types";

export const KIND_LABELS: Record<string, string> = {
  weekly_briefing: "Briefing",
  reminder: "Reminder",
  risk_warning: "Risk check",
  new_subscription: "Needs your call",
  missed_recurring: "Missed",
  backup_stale: "Backup",
  scheduled_question: "Scheduled answer",
  job_failed: "Job",
  recap: "Recap",
  categorize_done: "Categories",
};

/** Opens the notification's conversation (the server creates or reuses it), or its recap. */
export async function openNotification(item: NotificationItem): Promise<void> {
  if (item.kind === "recap" && typeof item.data.period_id === "string") {
    await markNotificationRead(item);
    goTo("recaps", item.data.period_id);
    return;
  }
  if (item.kind === "categorize_done") {
    await markNotificationRead(item);
    goTo("activity", "ledger");
    return;
  }
  const result = await api<NotificationChat>(`/api/notifications/${item.id}/chat`, {
    method: "POST",
  });
  window.dispatchEvent(
    new CustomEvent("arno:open-thread", { detail: result.thread_id }),
  );
}

export async function markNotificationRead(item: NotificationItem): Promise<void> {
  await api(`/api/notifications/${item.id}/read`, { method: "POST" });
}
