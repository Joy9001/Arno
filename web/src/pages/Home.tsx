import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { ArrowDownLeft, MessageCircle, Plus, Upload, X } from "lucide-react";
import { api, errorMessage } from "../api";
import { formatDate, formatMoment, formatPaise } from "../format";
import { Busy, Loading } from "../Loading";
import { movementLabel, movementNote } from "../movement";
import type { ActivityTab } from "./Activity";
import { TodaySummary, TodaySummarySkeleton, type HomePayload } from "../Today";
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

const QUICK_ACTIONS = [
  { label: "Log a spend", text: "Log a spend: ", Icon: Plus },
  { label: "Add income", text: "Add income: ", Icon: ArrowDownLeft },
  { label: "Import a statement", text: null, Icon: Upload },
  { label: "Check a purchase", text: "Can I afford ", Icon: MessageCircle },
];

type NeedActions = {
  onOpen: (item: NotificationItem) => void;
  onDismiss: (item: NotificationItem) => void;
  busy: string | null;
  dismissing: string | null;
};

function NeedRow({
  item,
  busy,
  dismissing,
  onOpen,
  onDismiss,
  compact = false,
}: { item: NotificationItem; compact?: boolean } & NeedActions) {
  const working = busy === item.id || dismissing === item.id;
  return (
    <article
      className={item.read_at ? "inbox-item" : "inbox-item unread"}
      aria-busy={working}
      onClick={() => {
        if (!working) onOpen(item);
      }}
    >
      <div className="inbox-head">
        <span className={`badge kind-${item.kind}`}>
          {KIND_LABELS[item.kind] ?? item.kind.replaceAll("_", " ")}
        </span>
        <span className="fine">{formatMoment(item.created_at)}</span>
      </div>
      <strong className="inbox-subject">{item.title}</strong>
      {!compact && item.body && <p className="inbox-body">{item.body}</p>}
      <div className="need-actions">
        <button
          type="button"
          className="ghost small"
          disabled={working}
          onClick={(event) => {
            event.stopPropagation();
            onOpen(item);
          }}
        >
          {busy === item.id ? (
            <>
              <Busy />
              Opening…
            </>
          ) : item.kind === "new_subscription" ? (
            "Decide"
          ) : (
            "Open"
          )}
        </button>
        {!item.read_at && (
          <button
            type="button"
            className="ghost small"
            disabled={working}
            onClick={(event) => {
              event.stopPropagation();
              onDismiss(item);
            }}
          >
            {dismissing === item.id ? (
              <>
                <Busy />
                Marking…
              </>
            ) : (
              "Mark read"
            )}
          </button>
        )}
      </div>
    </article>
  );
}

function AllNotifications({
  busy,
  dismissing,
  onOpen,
  onDismiss,
}: NeedActions) {
  const inbox = useApi<NotificationList>("/api/notifications?limit=100");
  if (inbox.loading) return <Loading label="Loading the queue…" />;
  if (inbox.error) {
    return (
      <div className="panel-error">
        <p className="error">{inbox.error}</p>
        <button type="button" className="ghost small" onClick={inbox.reload}>
          Retry
        </button>
      </div>
    );
  }
  if (!inbox.data?.notifications.length) {
    return <p className="compact-empty">Nothing needs you right now.</p>;
  }
  return (
    <div className="inbox-list">
      {inbox.data.notifications.map((item) => (
        <NeedRow
          key={item.id}
          item={item}
          busy={busy}
          dismissing={dismissing}
          onOpen={onOpen}
          onDismiss={onDismiss}
        />
      ))}
    </div>
  );
}

export function HomeRail({
  onOpenChat,
  onAsk,
  onOpenActivity,
  onInboxChanged,
  onOpenProjection,
  version = 0,
}: {
  onOpenChat: (threadId: string) => void;
  onAsk: (text: string) => void;
  onOpenActivity: (tab: ActivityTab) => void;
  onInboxChanged: () => void;
  onOpenProjection: () => void;
  version?: number;
}) {
  const [horizon, setHorizon] = useState(90);
  const home = useApi<HomePayload>(`/api/home?horizon_days=${horizon}&v=${version}`);
  const [busy, setBusy] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [queueOpen, setQueueOpen] = useState(false);
  const [queueVersion, setQueueVersion] = useState(0);
  const [openError, setOpenError] = useState<string | null>(null);

  async function openNeed(item: NotificationItem) {
    setBusy(item.id);
    setOpenError(null);
    try {
      const result = await api<NotificationChat>(`/api/notifications/${item.id}/chat`, {
        method: "POST",
      });
      home.reload();
      onInboxChanged();
      setQueueOpen(false);
      onOpenChat(result.thread_id);
    } catch (caught) {
      setOpenError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function dismissNeed(item: NotificationItem) {
    setDismissing(item.id);
    setOpenError(null);
    try {
      await api(`/api/notifications/${item.id}/read`, { method: "POST" });
      home.reload();
      onInboxChanged();
      setQueueVersion((value) => value + 1);
    } catch (caught) {
      setOpenError(errorMessage(caught));
    } finally {
      setDismissing(null);
    }
  }

  const needs = (home.data?.needs_you ?? []).filter((item) => !item.read_at);

  return (
    <div className="home-stack">
      {home.error ? (
        <div className="today today-state">
          <p>Home couldn't load its numbers.</p>
          <button type="button" className="ghost small" onClick={home.reload}>
            Retry
          </button>
        </div>
      ) : home.data ? (
        <TodaySummary
          data={home.data}
          horizon={horizon}
          onHorizon={setHorizon}
          onOpenProjection={onOpenProjection}
          busy={home.loading}
        />
      ) : (
        <TodaySummarySkeleton />
      )}

      <div className="quick">
        {QUICK_ACTIONS.map(({ label, text, Icon }) => (
          <button
            key={label}
            type="button"
            onClick={() => (text === null ? onOpenActivity("imports") : onAsk(text))}
          >
            <Icon size={15} strokeWidth={1.8} aria-hidden="true" />
            {label}
          </button>
        ))}
      </div>

      {openError && <p className="error">{openError}</p>}

      {home.data && (
        <>
          <section className="home-block">
            <div className="block-head">
              <h2>Needs you</h2>
              <div className="block-actions">
                <span className="fine">
                  {home.data.unread_count > 0
                    ? `${home.data.unread_count} unread`
                    : "Nothing unread"}
                </span>
                <button type="button" className="ghost small" onClick={() => setQueueOpen(true)}>
                  View all
                </button>
              </div>
            </div>
            {needs.length === 0 ? (
              <p className="compact-empty">
                {home.data.unread_count > 0
                  ? "Newer items are read. Open View all for older unread items."
                  : "Nothing needs you right now."}
              </p>
            ) : (
              <div className="inbox-list">
                {needs.slice(0, 2).map((item) => (
                  <NeedRow
                    key={item.id}
                    item={item}
                    compact
                    busy={busy}
                    dismissing={dismissing}
                    onOpen={(need) => void openNeed(need)}
                    onDismiss={(need) => void dismissNeed(need)}
                  />
                ))}
              </div>
            )}
          </section>

          <section className="home-block">
            <div className="block-head">
              <h2>Recent activity</h2>
              <button
                type="button"
                className="ghost small"
                onClick={() => onOpenActivity("ledger")}
              >
                View ledger
              </button>
            </div>
            {home.data.recent.length === 0 ? (
              <p className="compact-empty">Nothing recorded yet.</p>
            ) : (
              <div className="recent-list">
                {home.data.recent.slice(0, 3).map((movement) => (
                  <div className="recent-row" key={movement.id}>
                    <div>
                      <strong title={movement.description}>{movementLabel(movement)}</strong>
                      <span className="fine">
                        {formatDate(movement.date)} · {movementNote(movement)}
                        {movement.reversed ? " · reversed" : ""}
                      </span>
                    </div>
                    <span
                      className={
                        movement.reversed ? "recent-amount reversed" : "recent-amount"
                      }
                    >
                      {formatPaise(movement.amount_paise)}
                    </span>
                  </div>
                ))}
              </div>
            )}
          </section>
        </>
      )}

      {queueOpen && (
        <Dialog.Root open onOpenChange={(open) => !open && setQueueOpen(false)}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay" />
            <Dialog.Content className="queue-dialog" aria-describedby={undefined}>
              <div className="queue-head">
                <Dialog.Title>All notifications</Dialog.Title>
                <Dialog.Close asChild>
                  <button type="button" className="ghost small" aria-label="Close">
                    <X size={15} strokeWidth={1.8} aria-hidden="true" />
                  </button>
                </Dialog.Close>
              </div>
              <AllNotifications
                key={queueVersion}
                busy={busy}
                dismissing={dismissing}
                onOpen={(need) => void openNeed(need)}
                onDismiss={(need) => void dismissNeed(need)}
              />
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </div>
  );
}
