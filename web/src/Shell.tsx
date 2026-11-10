import { Fragment, useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Theme } from "@radix-ui/themes";
import {
  AlertCircle,
  CirclePlay,
  List,
  MessageSquare,
  Settings,
  Sparkle,
  Wallet,
  type LucideIcon,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { api, errorMessage } from "./api";
import { ASK_EVENT, ATTACH_EVENT, type AskDetail } from "./ask";
import { logout, type User } from "./auth";
import { Assistant } from "./chat/Chat";
import { JobToasts } from "./components/job-toasts";
import { EvidencePanel } from "./EvidencePanel";
import { EVIDENCE_EVENT, PLANNER_EVENT, RUN_EVENT } from "./evidence";
import {
  activeJobSummary,
  jobRailLabel,
  jobTitle,
  useJobs,
  useNow,
  userFacingJobs,
} from "./jobs";
import { Busy } from "./Loading";
import { RunPage, RunPanel } from "./run/Run";
import { NAVIGATE_EVENT, UserContext, goTo, type PageId } from "./nav";
import { Orb } from "./Orb";
import { ActivityPage, type ActivityTab } from "./pages/Activity";
import { MoneyPage, type MoneyTab } from "./pages/Money";
import { PlannerPanel } from "./panels/Planner";
import { RecapsPage } from "./pages/Recaps";
import { SettingsPanel } from "./panels/Settings";
import type { ProjectionPayload } from "./projection";
import type { JobActivity, NotificationList, RecapList } from "./types";

const NAV: Array<{ id: PageId; label: string; title: string; Icon: LucideIcon }> = [
  { id: "ask", label: "Ask", title: "Ask", Icon: Sparkle },
  { id: "chats", label: "Chats", title: "Chats", Icon: MessageSquare },
  { id: "money", label: "Money", title: "Money", Icon: Wallet },
  { id: "activity", label: "Activity", title: "Activity", Icon: List },
  { id: "recaps", label: "Recaps", title: "Recaps", Icon: CirclePlay },
];
const SETTINGS = { id: "settings" as const, label: "Settings", title: "Settings", Icon: Settings };
const PAGES = [...NAV, SETTINGS];
// Phones keep four areas and More; Chats lives in the conversation's own History.
const TABBAR: PageId[] = ["ask", "money", "activity", "recaps", "settings"];

const MONEY_TABS: MoneyTab[] = ["timeline", "accounts", "schedules", "goals", "people"];
const ACTIVITY_TABS: ActivityTab[] = ["ledger", "imports", "inbox"];

/** A live, quiet sign that the agent is working, for the rail and the phone tab bar. */
function BackgroundWork({ variant }: { variant: "rail" | "tabbar" }) {
  const active = useJobs((state) => state.active);
  const jobs = userFacingJobs(active);
  const now = useNow(jobs.length > 0);
  if (!jobs.length) return null;
  const label =
    jobs.length === 1 ? jobRailLabel(jobs[0].kind) : `${jobs.length} tasks running`;
  return (
    <button
      type="button"
      className={variant === "rail" ? "rail-job" : "tabbar-job"}
      aria-label={`Background work: ${activeJobSummary(active, now)}`}
      onClick={() => goTo("settings", "jobs")}
    >
      <span className="job-spin" aria-hidden="true">
        <Busy />
      </span>
      <span className="rail-job-label">{label}</span>
    </button>
  );
}

/** Announced only when the set of running jobs changes, not on every clock tick. */
function BackgroundWorkStatus() {
  const active = useJobs((state) => state.active);
  const jobs = userFacingJobs(active);
  return (
    <p className="sr-only" role="status" aria-live="polite">
      {jobs.length ? `Background work: ${jobs.map(jobTitle).join(", ")}` : ""}
    </p>
  );
}

/** The 1180px edge is where the canvas leaves the side of the conversation for a sheet. */
function useCompactViewport(): boolean {
  const [compact, setCompact] = useState(
    () => window.matchMedia("(max-width: 1180px)").matches,
  );
  useEffect(() => {
    const query = window.matchMedia("(max-width: 1180px)");
    const handler = () => setCompact(query.matches);
    query.addEventListener("change", handler);
    return () => query.removeEventListener("change", handler);
  }, []);
  return compact;
}

const THREAD_ID = /^[A-Za-z0-9_-]{1,120}$/;

type OpenThreadDetail =
  | string
  | { threadId?: unknown; messageId?: unknown }
  | null;

/**
 * Routes: #ask, #chats/<thread>/<message>, #money/<tab>, #activity/<tab>[/<import>], #recaps,
 * #settings/<section>. Older links keep working: #home and #home/<thread>/<message> open the
 * conversation, and #money/projection opens the timeline.
 */
function readHash(): {
  page: PageId;
  money: MoneyTab;
  activity: ActivityTab;
  thread: string | null;
  message: string | null;
  importId: string | null;
  runId: string | null;
} {
  const [rawPage, rawTab, rawMessage] = window.location.hash.slice(1).split("/");
  const legacyHome = rawPage === "home";
  const threadLike = Boolean(rawTab && THREAD_ID.test(rawTab));
  let page: PageId = PAGES.some((item) => item.id === rawPage) ? (rawPage as PageId) : "ask";
  if (legacyHome) page = threadLike ? "chats" : "ask";
  const money = rawTab === "projection"
    ? "timeline"
    : MONEY_TABS.includes(rawTab as MoneyTab)
      ? (rawTab as MoneyTab)
      : "timeline";
  const activity = ACTIVITY_TABS.includes(rawTab as ActivityTab)
    ? (rawTab as ActivityTab)
    : "ledger";
  const thread = page === "chats" && threadLike ? rawTab : null;
  const message =
    thread && rawMessage && THREAD_ID.test(rawMessage) ? rawMessage : null;
  const importId =
    page === "activity" &&
    activity === "imports" &&
    rawMessage &&
    THREAD_ID.test(rawMessage)
      ? rawMessage
      : null;
  const runId =
    page === "activity" && rawTab === "run" && rawMessage && TASK_ID.test(rawMessage)
      ? rawMessage
      : null;
  return { page, money, activity, thread, message, importId, runId };
}

const TASK_ID = /^[0-9a-f]{32}$/;

function openThreadDetail(detail: OpenThreadDetail): {
  thread: string;
  message: string | null;
} {
  if (typeof detail === "string") return { thread: detail.trim(), message: null };
  const thread = String(detail?.threadId ?? "").trim();
  const message = String(detail?.messageId ?? "").trim();
  return { thread, message: message || null };
}

async function threadExists(threadId: string): Promise<boolean> {
  try {
    await api<unknown>(`/api/threads/${encodeURIComponent(threadId)}/messages`);
    return true;
  } catch {
    return false;
  }
}

export function Shell({ user, onSignedOut }: { user: User | null; onSignedOut: () => void }) {
  const initial = readHash();
  const [page, setPage] = useState<PageId>(initial.page);
  const [moneyTab, setMoneyTab] = useState<MoneyTab>(initial.money);
  const [activityTab, setActivityTab] = useState<ActivityTab>(initial.activity);
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [recapsNew, setRecapsNew] = useState(0);
  const [focusThreadId, setFocusThreadId] = useState<string | null>(null);
  const [focusMessageId, setFocusMessageId] = useState<string | null>(null);
  const [focusImportId, setFocusImportId] = useState<string | null>(initial.importId);
  const [newChatSignal, setNewChatSignal] = useState(initial.page === "ask" ? 1 : 0);
  const [seed, setSeed] = useState<{ id: number; text: string; send?: boolean } | null>(null);
  const [attachments, setAttachments] = useState<{ id: number; files: File[] } | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [evidence, setEvidence] = useState<ProjectionPayload | null>(null);
  const [planner, setPlanner] = useState<ProjectionPayload | null>(null);
  const [runTask, setRunTask] = useState<string | null>(null);
  /** The canvas is about the open conversation, so leaving it closes the canvas. */
  const closeCanvas = useCallback(() => {
    setEvidence(null);
    setPlanner(null);
    setRunTask(null);
  }, []);
  const [runPage, setRunPage] = useState<string | null>(initial.runId);
  const compact = useCompactViewport();
  const talking = page === "ask" || page === "chats";

  const go = useCallback((next: PageId, sub?: string) => {
    setEvidence(null);
    setPlanner(null);
    setRunTask(null);
    setRunPage(next === "activity" && sub?.startsWith("run/") ? sub.slice(4) : null);
    setPage(next);
    if (!(next === "activity" && sub?.startsWith("imports/"))) {
      setFocusImportId(null);
    }
    const hash = sub ? `${next}/${sub}` : next;
    if (window.location.hash !== `#${hash}`) {
      window.history.pushState(null, "", `#${hash}`);
    }
  }, []);

  useEffect(() => {
    const onLocation = () => {
      const next = readHash();
      setPage(next.page);
      setMoneyTab(next.money);
      setActivityTab(next.activity);
      setFocusImportId(next.importId);
      setRunPage(next.runId);
      setEvidence(null);
      if (next.thread) {
        void threadExists(next.thread).then((exists) => {
          setFocusThreadId(exists ? next.thread : null);
          setFocusMessageId(exists ? next.message : null);
        });
      } else {
        setFocusThreadId(null);
        setFocusMessageId(null);
      }
    };
    window.addEventListener("popstate", onLocation);
    window.addEventListener("hashchange", onLocation);
    return () => {
      window.removeEventListener("popstate", onLocation);
      window.removeEventListener("hashchange", onLocation);
    };
  }, []);

  // A deep link such as #chats/cnv_… opens that conversation, but only when the thread really
  // exists; otherwise the link falls back to the conversation list.
  useEffect(() => {
    const { thread, message } = readHash();
    if (window.location.hash.startsWith("#home")) {
      window.history.replaceState(null, "", thread ? `#chats/${thread}` : "#ask");
    }
    if (!thread) return;
    void threadExists(thread).then((exists) => {
      if (exists) {
        setFocusThreadId(thread);
        setFocusMessageId(message);
      } else {
        window.history.replaceState(null, "", "#chats");
      }
    });
  }, []);

  useEffect(() => {
    const onOpenThread = (event: Event) => {
      const { thread, message } = openThreadDetail(
        (event as CustomEvent<OpenThreadDetail>).detail,
      );
      if (!THREAD_ID.test(thread) || (message && !THREAD_ID.test(message))) return;
      void threadExists(thread).then((exists) => {
        if (!exists) return;
        setFocusThreadId(thread);
        setFocusMessageId(message);
        go("chats", message ? `${thread}/${message}` : thread);
      });
    };
    window.addEventListener("arno:open-thread", onOpenThread);
    return () => window.removeEventListener("arno:open-thread", onOpenThread);
  }, [go]);

  useEffect(() => {
    const onNavigate = (event: Event) => {
      const { page: next, sub } = (event as CustomEvent<{ page: PageId; sub?: string }>).detail;
      if (!PAGES.some((item) => item.id === next)) return;
      if (next === "money" && MONEY_TABS.includes(sub as MoneyTab)) setMoneyTab(sub as MoneyTab);
      if (next === "activity" && ACTIVITY_TABS.includes(sub as ActivityTab)) {
        setActivityTab(sub as ActivityTab);
      }
      go(next, sub);
    };
    window.addEventListener(NAVIGATE_EVENT, onNavigate);
    return () => window.removeEventListener(NAVIGATE_EVENT, onNavigate);
  }, [go]);

  const refreshUnread = useCallback(async () => {
    try {
      const inbox = await api<NotificationList>("/api/notifications?limit=1");
      setUnread(inbox.unread_count);
    } catch {
      // The badge is best effort; the inbox shows its own error.
    }
  }, []);

  const refreshRecaps = useCallback(async () => {
    try {
      const recaps = await api<Pick<RecapList, "unseen">>("/api/recaps/unseen");
      setRecapsNew(recaps.unseen);
    } catch {
      // The badge is best effort; the page shows its own error.
    }
  }, []);

  // Asking from a data page carries the question into the conversation.
  useEffect(() => {
    const onAsk = (event: Event) => {
      const detail = (event as CustomEvent<AskDetail | string>).detail ?? "";
      const ask = typeof detail === "string" ? { text: detail, send: false } : detail;
      setSeed(ask.text ? { id: Date.now(), ...ask } : null);
      setFocusSignal((value) => value + 1);
      setEvidence(null);
      if (page !== "ask" && page !== "chats") go("chats");
    };
    window.addEventListener(ASK_EVENT, onAsk);
    return () => window.removeEventListener(ASK_EVENT, onAsk);
  }, [page, go]);

  useEffect(() => {
    const onAttach = (event: Event) => {
      const files = (event as CustomEvent<File[]>).detail ?? [];
      if (!files.length) return;
      setAttachments({ id: Date.now(), files });
      setFocusSignal((value) => value + 1);
      setEvidence(null);
      if (page !== "ask" && page !== "chats") go("chats");
    };
    window.addEventListener(ATTACH_EVENT, onAttach);
    return () => window.removeEventListener(ATTACH_EVENT, onAttach);
  }, [page, go]);

  useEffect(() => {
    const onEvidence = (event: Event) => {
      const payload = (event as CustomEvent<ProjectionPayload>).detail;
      if (!payload || !Array.isArray(payload.curve)) return;
      setPlanner(null);
      setRunTask(null);
      setEvidence(payload);
    };
    const onPlanner = (event: Event) => {
      const payload = (event as CustomEvent<ProjectionPayload>).detail;
      if (!payload || !Array.isArray(payload.curve)) return;
      setEvidence(null);
      setRunTask(null);
      setPlanner(payload);
    };
    const onRun = (event: Event) => {
      const taskId = (event as CustomEvent<string>).detail;
      if (!TASK_ID.test(taskId ?? "")) return;
      setEvidence(null);
      setPlanner(null);
      setRunTask(taskId);
    };
    window.addEventListener(EVIDENCE_EVENT, onEvidence);
    window.addEventListener(PLANNER_EVENT, onPlanner);
    window.addEventListener(RUN_EVENT, onRun);
    return () => {
      window.removeEventListener(EVIDENCE_EVENT, onEvidence);
      window.removeEventListener(PLANNER_EVENT, onPlanner);
      window.removeEventListener(RUN_EVENT, onRun);
    };
  }, []);

  // One loop for the badges and background work: fast while a job is active, idle otherwise.
  const applyActivity = useJobs((state) => state.applyActivity);
  const kick = useJobs((state) => state.kick);
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    let lastBadge = 0;

    const poll = async () => {
      // Jobs may poll every 3s; the inbox and recap badges keep their 30s pace.
      const badgeDue = Date.now() - lastBadge >= 30_000;
      if (badgeDue) {
        lastBadge = Date.now();
        void refreshUnread();
        void refreshRecaps();
      }
      let delay = 30_000;
      try {
        const since = useJobs.getState().since;
        const query = since ? `?since=${encodeURIComponent(since)}` : "";
        const activity = await api<JobActivity>(`/api/jobs/activity${query}`);
        if (cancelled) return;
        applyActivity(activity);
        if (activity.finished.length) void refreshUnread();
        // Fast only while something runs or is about to: a batch can wait 30 min between checks.
        const soon = Date.now() + 30_000;
        const busy = activity.active.some(
          (job) => job.status === "running" || new Date(job.run_at).getTime() <= soon,
        );
        delay = busy ? 3_000 : 30_000;
      } catch {
        // Best effort: a badge or an indicator; the next tick tries again.
      }
      if (!cancelled) timer = window.setTimeout(() => void poll(), delay);
    };

    timer = window.setTimeout(() => void poll(), 0);
    return () => {
      cancelled = true;
      if (timer !== undefined) window.clearTimeout(timer);
    };
  }, [refreshUnread, refreshRecaps, applyActivity, kick]);

  // Opening a recap clears its New mark on the page; the rail follows at once.
  useEffect(() => {
    const onSeen = () => void refreshRecaps();
    window.addEventListener("arno:recaps-seen", onSeen);
    return () => window.removeEventListener("arno:recaps-seen", onSeen);
  }, [refreshRecaps]);

  async function signOut() {
    try {
      await logout();
      onSignedOut();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function navigate(id: PageId) {
    if (id === "ask") setNewChatSignal((value) => value + 1);
    if (id === "money") go("money", moneyTab);
    else if (id === "activity") go("activity", activityTab);
    else go(id);
    void refreshUnread();
    void refreshRecaps();
  }

  const plannerPanel = planner ? (
    <PlannerPanel key={JSON.stringify(planner.hypothetical ?? null)} seed={planner} onClose={() => setPlanner(null)} />
  ) : null;

  const evidencePanel = evidence ? (
    <EvidencePanel
      payload={evidence}
      onClose={() => setEvidence(null)}
      onOpenProjection={() => {
        setEvidence(null);
        setMoneyTab("timeline");
        go("money", "timeline");
      }}
      onAsk={() => {
        setEvidence(null);
        setFocusSignal((value) => value + 1);
      }}
    />
  ) : null;

  const runPanel = runTask ? (
    <RunPanel key={runTask} taskId={runTask} onClose={() => setRunTask(null)} />
  ) : null;

  const canvasPanel = plannerPanel ?? evidencePanel ?? runPanel;
  const current = PAGES.find((item) => item.id === page) ?? NAV[0];

  return (
    <UserContext.Provider value={user}>
      <div className="app" data-page={page}>
        <nav className="rail" aria-label="Primary">
          <button
            type="button"
            className="rail-brand"
            aria-label="Arno, ask"
            onClick={() => navigate("ask")}
          >
            <Orb size={38} live />
          </button>
          {NAV.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              className={page === id ? "rail-item on" : "rail-item"}
              aria-current={page === id ? "page" : undefined}
              aria-label={
                id === "activity" && unread > 0
                  ? `${label}, ${unread} unread`
                  : id === "recaps" && recapsNew > 0
                    ? `${label}, ${recapsNew} new`
                    : label
              }
              onClick={() => navigate(id)}
            >
              {id === "activity" && unread > 0 && (
                <span className="rail-badge" aria-hidden="true">
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
              {id === "recaps" && recapsNew > 0 && (
                <span className="rail-badge" aria-hidden="true">
                  {recapsNew > 99 ? "99+" : recapsNew}
                </span>
              )}
              <Icon size={21} strokeWidth={1.9} aria-hidden="true" />
              <span>{label}</span>
            </button>
          ))}
          <BackgroundWork variant="rail" />
          <button
            type="button"
            className={page === "settings" ? "rail-item rail-foot on" : "rail-item rail-foot"}
            aria-current={page === "settings" ? "page" : undefined}
            onClick={() => navigate("settings")}
          >
            <Settings size={21} strokeWidth={1.9} aria-hidden="true" />
            <span>Settings</span>
          </button>
        </nav>

        <div className="stage">
          <h1 className="sr-only">{current.title}</h1>
          {error && (
            <p className="topbar-error" role="alert">
              <AlertCircle size={15} strokeWidth={1.8} aria-hidden="true" />
              {error}
            </p>
          )}

          {/* One conversation, always mounted, so a reply keeps streaming while you look
              at your money. Ask shows it as the agent's home; Chats adds the list. */}
          <div
            className={talking ? "talk" : "talk talk-hidden"}
            data-mode={page === "chats" ? "chats" : "ask"}
            data-canvas={canvasPanel && !compact ? "open" : undefined}
            hidden={!talking}
          >
            <Assistant
              seed={seed}
              focusSignal={focusSignal}
              onSeedConsumed={() => setSeed(null)}
              attachments={attachments}
              onAttachmentsConsumed={() => setAttachments(null)}
              focusThreadId={focusThreadId}
              focusMessageId={focusMessageId}
              onFocusHandled={() => {
                setFocusThreadId(null);
                setFocusMessageId(null);
              }}
              newChatSignal={newChatSignal}
              listInline={page === "chats" && !compact}
              onConversationStarted={() => {
                if (page === "ask") go("chats");
              }}
              onConversationLeft={closeCanvas}
              onActivity={() => void refreshUnread()}
              onEvidence={setEvidence}
            />
            <AnimatePresence initial={false}>
              {talking && !compact && canvasPanel && (
                <motion.aside
                  key="canvas"
                  className="canvas"
                  aria-label="Canvas"
                  initial={{ opacity: 0, x: 24 }}
                  animate={{ opacity: 1, x: 0 }}
                  exit={{ opacity: 0, x: 16 }}
                  transition={{ duration: 0.3, ease: [0.22, 1, 0.36, 1] }}
                >
                  {canvasPanel}
                </motion.aside>
              )}
            </AnimatePresence>
          </div>

          {page === "money" && (
            <MoneyPage
              tab={moneyTab}
              onTab={(tab) => {
                setMoneyTab(tab);
                go("money", tab);
              }}
            />
          )}
          {page === "activity" && runPage && <RunPage key={runPage} taskId={runPage} />}
          {page === "activity" && !runPage && (
            <ActivityPage
              tab={activityTab}
              importId={focusImportId}
              unread={unread}
              onInboxChanged={() => void refreshUnread()}
              onTab={(tab) => {
                setActivityTab(tab);
                setFocusImportId(null);
                go("activity", tab);
              }}
              onImportId={(importId) => {
                setFocusImportId(importId);
                go("activity", `imports/${importId}`);
              }}
            />
          )}
          {page === "recaps" && <RecapsPage />}
          {page === "settings" && <SettingsPanel onSignOut={() => void signOut()} />}
        </div>

        {compact && canvasPanel && (
          <Dialog.Root
            open
            onOpenChange={(open) => {
              if (open) return;
              closeCanvas();
            }}
          >
            <Dialog.Portal>
              <Dialog.Overlay className="dialog-overlay sheet-overlay" />
              <Dialog.Content className="sheet-dialog evidence-sheet" aria-describedby={undefined}>
                <Dialog.Title className="sr-only">Canvas</Dialog.Title>
                <Theme
                  appearance="dark"
                  accentColor="lime"
                  grayColor="olive"
                  radius="large"
                  hasBackground={false}
                  className="sheet-theme"
                >
                  {canvasPanel}
                </Theme>
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        )}

        <nav className="tabbar" aria-label="Primary">
          {TABBAR.map((id) => {
            const item = PAGES.find((entry) => entry.id === id) ?? NAV[0];
            const on = page === id || (id === "ask" && page === "chats");
            const label = id === "settings" ? "More" : item.label;
            return (
              <Fragment key={id}>
                {id === "settings" && <BackgroundWork variant="tabbar" />}
                <button
                  type="button"
                  className={on ? "tabbar-item on" : "tabbar-item"}
                  aria-current={on ? "page" : undefined}
                  aria-label={
                    id === "activity" && unread > 0
                      ? `Activity, ${unread} unread`
                      : id === "recaps" && recapsNew > 0
                        ? `Recaps, ${recapsNew} new`
                        : label
                  }
                  onClick={() => navigate(id)}
                >
                  {id === "ask" ? (
                    <Orb size={24} />
                  ) : (
                    <item.Icon size={22} strokeWidth={1.9} aria-hidden="true" />
                  )}
                  <span>{label}</span>
                  {id === "activity" && unread > 0 && <span className="tabbar-badge">{unread}</span>}
                  {id === "recaps" && recapsNew > 0 && <span className="tabbar-badge">{recapsNew}</span>}
                </button>
              </Fragment>
            );
          })}
        </nav>

        <BackgroundWorkStatus />
        <JobToasts />
      </div>
    </UserContext.Provider>
  );
}
