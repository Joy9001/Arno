import { useState } from "react";
import { ArrowUpRight, Check, ChevronLeft, ChevronRight, CalendarClock } from "lucide-react";
import { api, errorMessage } from "../api";
import { askAgent } from "../ask";
import { formatMoment } from "../format";
import { Busy, Loading } from "../Loading";
import { goTo } from "../nav";
import { Orb } from "../Orb";
import type {
  NotificationItem,
  NotificationList,
  ScheduledQuestionList,
} from "../types";
import { useApi } from "../useApi";
import { KIND_LABELS, markNotificationRead, openNotification } from "../notifications";

type NeedActions = {
  onOpen: (item: NotificationItem) => void;
  onDismiss: (item: NotificationItem) => void;
  busy: string | null;
  dismissing: string | null;
};

function NeedRow({ item, busy, dismissing, onOpen, onDismiss }: { item: NotificationItem } & NeedActions) {
  const working = busy === item.id || dismissing === item.id;
  return (
    <article
      className={item.read_at ? "inbox-item" : "inbox-item unread"}
      data-kind={item.kind}
      aria-busy={working}
    >
      <span className="inbox-icon" aria-hidden="true">
        {item.kind === "new_subscription" ? "?" : item.kind === "reminder" ? "!" : item.read_at ? "✓" : "↗"}
      </span>
      <div className="inbox-main">
        <span className="inbox-kind">{KIND_LABELS[item.kind] ?? item.kind.replaceAll("_", " ")}</span>
        <strong className="inbox-subject">{item.title}</strong>
        {item.body && <p className="inbox-body">{item.body}</p>}
        <div className="need-actions">
          <button
            type="button"
            className={item.kind === "new_subscription" ? "pill-button lime" : "pill-button"}
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
                <ArrowUpRight size={14} strokeWidth={2} aria-hidden="true" />
                {item.kind === "new_subscription"
                  ? "Decide in chat"
                  : item.kind === "categorize_done"
                    ? "Review"
                    : "Open in chat"}
              </>
            )}
          </button>
          {!item.read_at && (
            <button
              type="button"
              className="pill-button"
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
                  <Check size={14} strokeWidth={2} aria-hidden="true" />
                  Mark read
                </>
              )}
            </button>
          )}
        </div>
      </div>
      <span className="inbox-time">{formatMoment(item.created_at)}</span>
    </article>
  );
}

/** Activity's inbox: what the agent noticed while you were away, and the questions you scheduled. */
export function InboxPanel({ onChanged }: { onChanged: () => void }) {
  const [page, setPage] = useState(0);
  const [cursors, setCursors] = useState<Array<string | null>>([null]);
  const [busy, setBusy] = useState<string | null>(null);
  const [dismissing, setDismissing] = useState<string | null>(null);
  const [actionError, setActionError] = useState<string | null>(null);
  const cursor = cursors[page];
  const inbox = useApi<NotificationList & { next_cursor: string | null }>(
    `/api/notifications?limit=25${cursor ? `&cursor=${encodeURIComponent(cursor)}` : ""}`,
  );
  const questions = useApi<ScheduledQuestionList>("/api/scheduled-questions");

  async function open(item: NotificationItem) {
    setBusy(item.id);
    setActionError(null);
    try {
      await openNotification(item);
      onChanged();
    } catch (caught) {
      setActionError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function dismiss(item: NotificationItem) {
    setDismissing(item.id);
    setActionError(null);
    try {
      await markNotificationRead(item);
      inbox.reload();
      onChanged();
    } catch (caught) {
      setActionError(errorMessage(caught));
    } finally {
      setDismissing(null);
    }
  }

  async function markAll() {
    setActionError(null);
    try {
      await api("/api/notifications/read-all", { method: "POST" });
      inbox.reload();
      onChanged();
    } catch (caught) {
      setActionError(errorMessage(caught));
    }
  }

  const notifications = inbox.data?.notifications ?? [];
  const next = inbox.data?.next_cursor;
  const active = (questions.data?.questions ?? []).filter((question) => question.active);

  return (
    <section className="panel inbox-panel">
      <div className="agent-note">
        <Orb size={26} />
        <p>
          I watch your money while you&apos;re away: <b>reminders</b>, <b>risk checks</b>,{" "}
          <b>patterns</b>, and the questions you scheduled. Nothing here changes your data without a
          tap.
        </p>
        {(inbox.data?.unread_count ?? 0) > 0 && (
          <button type="button" className="pill-button" onClick={() => void markAll()}>
            Mark all read
          </button>
        )}
      </div>
      {actionError && (
        <p className="error inline-error" role="alert">
          {actionError}
        </p>
      )}
      <div className="inbox-layout">
        <div>
          {inbox.loading && !inbox.data ? (
            <Loading label="Loading the inbox…" />
          ) : inbox.error ? (
            <div className="panel-error">
              <p className="error">{inbox.error}</p>
              <button type="button" className="pill-button" onClick={inbox.reload}>
                Try again
              </button>
            </div>
          ) : notifications.length ? (
            <div className="inbox-list">
              {notifications.map((item) => (
                <NeedRow
                  key={item.id}
                  item={item}
                  busy={busy}
                  dismissing={dismissing}
                  onOpen={(need) => void open(need)}
                  onDismiss={(need) => void dismiss(need)}
                />
              ))}
            </div>
          ) : (
            <p className="compact-empty">Nothing yet. Reminders, risks, and patterns land here.</p>
          )}
          {(page > 0 || next) && (
            <div className="button-row">
              <button type="button" className="pill-button" disabled={page === 0} onClick={() => setPage(page - 1)}>
                <ChevronLeft size={14} aria-hidden="true" /> Newer
              </button>
              <button
                type="button"
                className="pill-button"
                disabled={!next}
                onClick={() => {
                  if (!next) return;
                  setCursors((all) => [...all.slice(0, page + 1), next]);
                  setPage(page + 1);
                }}
              >
                Older <ChevronRight size={14} aria-hidden="true" />
              </button>
            </div>
          )}
        </div>
        <aside className="inbox-side" aria-label="Scheduled questions">
          <h2>Scheduled questions</h2>
          {questions.loading && !questions.data ? (
            <Loading label="Loading questions…" />
          ) : questions.error ? (
            <p className="error">{questions.error}</p>
          ) : (
            active.map((question) => (
              <div className="sched-q" key={question.id}>
                <b>“{question.prompt}”</b>
                <span>
                  {question.rule}
                  {question.next_run_at ? ` · next ${formatMoment(question.next_run_at)}` : ""}
                </span>
              </div>
            ))
          )}
          <button type="button" className="sched-q add" onClick={() => goTo("settings", "questions")}>
            <CalendarClock size={16} strokeWidth={1.9} aria-hidden="true" />
            <b>Schedule a question</b>
            <span>Read-only · I answer, I never change data</span>
          </button>
          <button type="button" className="pill-button ask" onClick={() => askAgent("")}>
            Ask about anything here
          </button>
        </aside>
      </div>
    </section>
  );
}
