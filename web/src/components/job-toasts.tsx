import { useState } from "react";
import { CircleAlert, CircleCheck, X } from "lucide-react";
import { Toast } from "radix-ui";
import { api, errorMessage } from "../api";
import { goTo } from "../nav";
import {
  dismissJobToast,
  jobOutcome,
  jobTitle,
  kickActivity,
  openUncategorized,
  shortJobError,
  useJobs,
  type JobToast,
} from "../jobs";
import type { JobView } from "../types";
import "./job-toasts.css";

type ToastAction = { label: string; run: () => void; retry?: boolean };

function uncategorizedCount(job: JobView): number {
  const value = job.result?.uncategorized;
  return typeof value === "number" ? value : 0;
}

function copyFor(job: JobView): { title: string; body: string; actions: ToastAction[] } {
  if (job.status === "failed") {
    return {
      title: `${jobTitle(job)} failed`,
      body: shortJobError(job.last_error),
      actions: [
        { label: "Retry", run: () => undefined, retry: true },
        { label: "Details", run: () => goTo("settings", "jobs") },
      ],
    };
  }

  if (job.kind === "categorize_import") {
    if (job.result?.status === "batch_submitted") {
      return {
        title: "Batch categorization sent",
        body: jobOutcome(job) ?? "Results will be ready in a few minutes.",
        actions: [],
      };
    }
    return {
      title: "Transactions categorized",
      body: jobOutcome(job) ?? "Your transactions are ready to review.",
      actions: uncategorizedCount(job) > 0 ? [{ label: "Review", run: openUncategorized }] : [],
    };
  }

  if (job.kind === "collect_batch") {
    return {
      title: "Batch results are in",
      body: jobOutcome(job) ?? "The batch finished categorizing your transactions.",
      actions: [{ label: "Review", run: openUncategorized }],
    };
  }

  if (job.kind === "scheduled_question") {
    return {
      title: "Your scheduled answer is ready",
      body: "Open it from your inbox.",
      actions: [{ label: "Open inbox", run: () => goTo("activity", "inbox") }],
    };
  }

  if (job.kind === "weekly_briefing") {
    return {
      title: "Your weekly briefing is ready",
      body: "Open it from your inbox.",
      actions: [{ label: "Open inbox", run: () => goTo("activity", "inbox") }],
    };
  }

  return {
    title: jobTitle(job),
    body: jobOutcome(job) ?? "Finished.",
    actions: [],
  };
}

/** The job toasts, mounted once by the shell. Success fades; a failure waits for you. */
export function JobToasts() {
  const toasts = useJobs((state) => state.toasts);
  const [retrying, setRetrying] = useState<string | null>(null);
  const [retryError, setRetryError] = useState<Record<string, string>>({});

  function dismiss(item: JobToast) {
    dismissJobToast(item.key);
    setRetryError((errors) => {
      if (!(item.key in errors)) return errors;
      const next = { ...errors };
      delete next[item.key];
      return next;
    });
  }

  async function retry(item: JobToast) {
    setRetrying(item.key);
    setRetryError((errors) => ({ ...errors, [item.key]: "" }));
    try {
      await api(`/api/jobs/${item.job.id}/retry`, { method: "POST" });
      dismiss(item);
      kickActivity();
    } catch (caught) {
      setRetryError((errors) => ({ ...errors, [item.key]: errorMessage(caught) }));
    } finally {
      setRetrying(null);
    }
  }

  return (
    <Toast.Provider swipeDirection="right">
      {toasts.map((item) => {
        const copy = copyFor(item.job);
        return (
          <Toast.Root
            key={item.key}
            className="job-toast"
            data-tone={item.tone}
            type={item.tone === "danger" ? "foreground" : "background"}
            duration={item.tone === "danger" ? Number.POSITIVE_INFINITY : 8000}
            onOpenChange={(open) => {
              if (!open) dismiss(item);
            }}
          >
            <span className="job-toast-icon" aria-hidden="true">
              {item.tone === "danger" ? (
                <CircleAlert size={18} strokeWidth={1.8} />
              ) : (
                <CircleCheck size={18} strokeWidth={1.8} />
              )}
            </span>
            <div className="job-toast-main">
              <Toast.Title className="job-toast-title">{copy.title}</Toast.Title>
              <Toast.Description className="job-toast-body">{copy.body}</Toast.Description>
              {retryError[item.key] && (
                <p className="job-toast-error" role="alert">
                  {retryError[item.key]}
                </p>
              )}
              {copy.actions.length > 0 && (
                <div className="job-toast-actions">
                  {copy.actions.map((action) => (
                    <Toast.Action
                      key={action.label}
                      className="job-toast-action"
                      altText={action.label}
                      disabled={retrying === item.key}
                      onClick={() => {
                        if (action.retry) void retry(item);
                        else {
                          action.run();
                          dismiss(item);
                        }
                      }}
                    >
                      {action.retry && retrying === item.key ? "Retrying…" : action.label}
                    </Toast.Action>
                  ))}
                </div>
              )}
            </div>
            <Toast.Close className="job-toast-close" aria-label="Dismiss">
              <X size={14} strokeWidth={2} aria-hidden="true" />
            </Toast.Close>
          </Toast.Root>
        );
      })}
      <Toast.Viewport className="job-toast-viewport" label="Background work" />
    </Toast.Provider>
  );
}
