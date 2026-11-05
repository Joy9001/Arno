import { useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { IconButton } from "@radix-ui/themes";
import {
  AlertCircle,
  ArrowDownLeft,
  ArrowUpRight,
  Check,
  CheckCircle2,
  ChevronLeft,
  ChevronRight,
  ListChecks,
  MessageCircle,
  Plus,
  Receipt,
  Upload,
  X,
} from "lucide-react";
import { api, errorMessage } from "../api";
import { formatDate, formatMoment, formatPaise } from "../format";
import { Busy, Loading } from "../Loading";
import { movementLabel, movementNote } from "../movement";
import type { ProjectionPayload } from "../projection";
import { TodaySummary, TodaySummarySkeleton, type HomePayload } from "../Today";
import type { ActivityTab } from "./Activity";
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

function todayLabel(): string {
  return new Date().toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

function StateCard({
  title,
  children,
  onRetry,
}: {
  title: string;
  children: string;
  onRetry: () => void;
}) {
  return (
    <div className="state-card" role="alert">
      <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
      <div>
        <strong>{title}</strong>
        <p className="fine">{children}</p>
        <button type="button" className="ghost small" onClick={onRetry}>
          Try again
        </button>
      </div>
    </div>
  );
}

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
          onClick={() => onOpen(item)}
        >
          {busy === item.id ? (
            <>
              <Busy />
              Opening…
            </>
          ) : (
            <>
              <ArrowUpRight size={13} strokeWidth={1.8} aria-hidden="true" />
              {item.kind === "new_subscription" ? "Decide" : "Open"}
            </>
          )}
        </button>
        {!item.read_at && (
          <button
            type="button"
            className="ghost small"
            disabled={working}
            onClick={() => onDismiss(item)}
          >
            {dismissing === item.id ? (
              <>
                <Busy />
                Marking…
              </>
            ) : (
              <>
                <Check size={13} strokeWidth={1.9} aria-hidden="true" />
                Mark read
              </>
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
  const [page, setPage] = useState(0);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const cursor = cursors[page];
  const inbox = useApi<NotificationList & { next_cursor: string | null }>(
    `/api/notifications?limit=25${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
  );
  if (inbox.loading) return <Loading label="Loading notifications…" />;
  if (inbox.error) {
    return (
      <div className="panel-error">
        <p className="error">{inbox.error}</p>
        <button type="button" className="ghost small" onClick={inbox.reload}>
          Try again
        </button>
      </div>
    );
  }
  const notifications = inbox.data?.notifications ?? [];
  const next = inbox.data?.next_cursor;
  return (
    <>
      {notifications.length ? (
        <div className="inbox-list bounded">
          {notifications.map((item) => (
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
      ) : (
        <p className="compact-empty">No notifications on this page.</p>
      )}
      {(page > 0 || next) && (
        <div className="button-row">
          <button type="button" className="ghost small" disabled={page === 0} onClick={() => setPage(page - 1)}>
            <ChevronLeft size={14} aria-hidden="true" /> Previous
          </button>
          <button
            type="button"
            className="ghost small"
            disabled={!next}
            onClick={() => {
              if (!next) return;
              setCursors((all) => [...all.slice(0, page + 1), next]);
              setPage(page + 1);
            }}
          >
            Next <ChevronRight size={14} aria-hidden="true" />
          </button>
        </div>
      )}
    </>
  );
}

export function HomeRail({
  onOpenChat,
  onAsk,
  onOpenActivity,
  onInboxChanged,
  onOpenProjection,
  onOpenEvidence,
  onOpenSettings,
  onClose,
  version = 0,
}: {
  onOpenChat: (threadId: string) => void;
  onAsk: (text: string) => void;
  onOpenActivity: (tab: ActivityTab) => void;
  onInboxChanged: () => void;
  onOpenProjection: () => void;
  onOpenEvidence: (payload: ProjectionPayload) => void;
  onOpenSettings: () => void;
  onClose?: () => void;
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

  const unread = (home.data?.needs_you ?? []).filter((item) => !item.read_at);

  return (
    <div className="today-panel">
      <header className="rail-head">
        <div>
          <h2>Today</h2>
          <p className="fine">{todayLabel()}</p>
        </div>
        <div className="rail-head-actions">
          {home.data && home.data.unread_count > 0 && (
            <span className="rail-count">{home.data.unread_count} unread</span>
          )}
          {onClose && (
            <IconButton
              type="button"
              variant="soft"
              color="gray"
              aria-label="Close Today"
              title="Close Today"
              onClick={onClose}
            >
              <X size={16} aria-hidden="true" />
            </IconButton>
          )}
        </div>
      </header>

      {home.error && !home.data ? (
        <StateCard title="Today couldn't load" onRetry={home.reload}>
          The numbers are unavailable right now. Nothing was changed.
        </StateCard>
      ) : home.data ? (
        <TodaySummary
          data={home.data}
          horizon={horizon}
          onHorizon={setHorizon}
          onOpenProjection={onOpenProjection}
          onOpenEvidence={onOpenEvidence}
          onOpenSettings={onOpenSettings}
          busy={home.loading}
        />
      ) : (
        <TodaySummarySkeleton />
      )}

      <div className="quick" aria-label="Quick actions">
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

      {home.error && home.data && (
        <p className="error inline-error" role="alert">
          {home.error}
        </p>
      )}
      {openError && (
        <p className="error inline-error" role="alert">
          {openError}
        </p>
      )}

      {home.data && (
        <>
          <section className="home-block" aria-labelledby="needs-you-heading">
            <div className="block-head">
              <h2 id="needs-you-heading">Updates</h2>
              <div className="block-actions">
                <span className="fine">
                  {home.data.unread_count > 0
                    ? `${home.data.unread_count} unread`
                    : "All read"}
                </span>
                <button type="button" className="ghost small" onClick={() => setQueueOpen(true)}>
                  View all
                  <ChevronRight size={13} strokeWidth={1.9} aria-hidden="true" />
                </button>
              </div>
            </div>
            {unread.length === 0 ? (
              <p className="compact-empty quiet">
                <CheckCircle2 size={16} strokeWidth={1.8} aria-hidden="true" />
                No unread updates.
              </p>
            ) : (
              <div className="inbox-list">
                {unread.slice(0, 2).map((item) => (
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
                {home.data.unread_count > 2 && (
                  <button
                    type="button"
                    className="ghost small more-row"
                    onClick={() => setQueueOpen(true)}
                  >
                    <ListChecks size={14} strokeWidth={1.8} aria-hidden="true" />
                    {home.data.unread_count - 2} more unread
                  </button>
                )}
              </div>
            )}
          </section>

          <section className="home-block" aria-labelledby="recent-heading">
            <div className="block-head">
              <h2 id="recent-heading">Recent activity</h2>
              <button
                type="button"
                className="ghost small"
                onClick={() => onOpenActivity("ledger")}
              >
                View ledger
                <ChevronRight size={13} strokeWidth={1.9} aria-hidden="true" />
              </button>
            </div>
            {home.data.recent.length === 0 ? (
              <p className="compact-empty quiet">
                <Receipt size={16} strokeWidth={1.8} aria-hidden="true" />
                Nothing recorded yet.
              </p>
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
                <div>
                  <Dialog.Title>All notifications</Dialog.Title>
                  <p className="fine">
                    {home.data ? `${home.data.unread_count} unread` : "Your inbox"} · newest first
                  </p>
                </div>
                <Dialog.Close asChild>
                  <button
                    type="button"
                    className="ghost small icon-only"
                    aria-label="Close notifications"
                    title="Close notifications"
                  >
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
