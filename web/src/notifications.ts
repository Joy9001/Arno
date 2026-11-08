import { api } from "./api";
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
};

/** Opens the notification's conversation (the server creates or reuses it). */
export async function openNotification(item: NotificationItem): Promise<void> {
  const result = await api<NotificationChat>(`/api/notifications/${item.id}/chat`, {
    method: "POST",
  });
  window.dispatchEvent(
    new CustomEvent("fin-agent:open-thread", { detail: result.thread_id }),
  );
}

export async function markNotificationRead(item: NotificationItem): Promise<void> {
  await api(`/api/notifications/${item.id}/read`, { method: "POST" });
}
