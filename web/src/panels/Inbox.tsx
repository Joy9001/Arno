import { useState } from "react";
import { api, errorMessage } from "../api";
import { formatMoment } from "../format";
import type { NotificationChat, NotificationItem, NotificationList } from "../types";
import { useApi } from "../useApi";

const KIND_LABELS: Record<string, string> = {
  weekly_briefing: "Briefing",
  reminder: "Reminder",
  risk_warning: "Risk",
  new_subscription: "Proposal",
  missed_recurring: "Missed",
  backup_stale: "Backup",
  scheduled_question: "Scheduled answer",
  job_failed: "Job",
};

function KindBadge({ kind }: { kind: string }) {
  return <span className={`badge kind-${kind}`}>{KIND_LABELS[kind] ?? kind.replaceAll("_", " ")}</span>;
}

export function InboxPanel({
  onOpenChat,
  onChanged,
}: {
  onOpenChat: (threadId: string) => void;
  onChanged: () => void;
}) {
  const inbox = useApi<NotificationList>("/api/notifications?limit=100");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function markRead(item: NotificationItem) {
    try {
      await api(`/api/notifications/${item.id}/read`, { method: "POST" });
      inbox.reload();
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function openInChat(item: NotificationItem) {
    setBusy(item.id);
    setError(null);
    try {
      const result = await api<NotificationChat>(`/api/notifications/${item.id}/chat`, {
        method: "POST",
      });
      inbox.reload();
      onChanged();
      onOpenChat(result.thread_id);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Inbox</p>
          <h2>What Fin-Agent noticed</h2>
          <p className="muted">
            Briefings, reminders, projection warnings, and proposals. Tapping one opens a new chat
            seeded with it, so you can ask a follow-up or accept a proposal. Proposals never write
            on their own.
          </p>
        </div>
        <button
          type="button"
          className="ghost"
          disabled={!inbox.data?.unread_count}
          onClick={async () => {
            try {
              await api("/api/notifications/read-all", { method: "POST" });
              inbox.reload();
              onChanged();
            } catch (caught) {
              setError(errorMessage(caught));
            }
          }}
        >
          Mark all read
        </button>
      </header>

      {error && <p className="error">{error}</p>}
      {inbox.loading && <p className="muted">Loading the inbox…</p>}
      {inbox.error && <p className="error">{inbox.error}</p>}
      {inbox.data && inbox.data.notifications.length === 0 && (
        <div className="empty-chat">
          <h3>Nothing yet.</h3>
          <p className="muted">
            The weekly briefing, reminders, risk checks, and pattern detection land here.
          </p>
        </div>
      )}

      <div className="inbox-list">
        {inbox.data?.notifications.map((item) => (
          <article
            key={item.id}
            className={item.read_at ? "inbox-item" : "inbox-item unread"}
            onClick={(event) => {
              if ((event.target as HTMLElement).closest("button")) return;
              void openInChat(item);
            }}
          >
            <div className="inbox-head">
              <div className="inbox-title">
                <KindBadge kind={item.kind} />
                <strong>{item.title}</strong>
              </div>
              <span className="fine">{formatMoment(item.created_at)}</span>
            </div>
            {item.body && <p className="inbox-body">{item.body}</p>}
            <div className="button-row">
              <button
                type="button"
                disabled={busy === item.id}
                onClick={() => void openInChat(item)}
              >
                {busy === item.id ? "Opening…" : "Open in chat"}
              </button>
              {!item.read_at && (
                <button
                  type="button"
                  className="ghost small"
                  onClick={() => void markRead(item)}
                >
                  Mark read
                </button>
              )}
            </div>
          </article>
        ))}
      </div>
    </section>
  );
}
