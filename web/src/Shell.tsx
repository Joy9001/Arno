import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Button, Theme } from "@radix-ui/themes";
import {
  AlertCircle,
  CalendarDays,
  Home as HomeIcon,
  List,
  LogOut,
  MessageCircle,
  Settings,
  ShieldCheck,
  Wallet,
} from "lucide-react";
import { AnimatePresence, motion } from "motion/react";
import { api, errorMessage } from "./api";
import { ASK_EVENT } from "./ask";
import { logout, type User } from "./auth";
import { Assistant } from "./chat/Chat";
import { EvidencePanel } from "./EvidencePanel";
import { EVIDENCE_EVENT } from "./evidence";
import { Mark } from "./Mark";
import { ActivityPage, type ActivityTab } from "./pages/Activity";
import { HomeRail } from "./pages/Home";
import { MoneyPage, type MoneyTab } from "./pages/Money";
import { SettingsPanel } from "./panels/Settings";
import type { ProjectionPayload } from "./projection";
import type { NotificationList } from "./types";

type PageId = "home" | "money" | "activity" | "settings";

const NAV: Array<{
  id: PageId;
  label: string;
  title: string;
  Icon: typeof HomeIcon;
}> = [
  { id: "home", label: "Home", title: "Conversation", Icon: HomeIcon },
  { id: "money", label: "Money", title: "Money", Icon: Wallet },
  { id: "activity", label: "Activity", title: "Activity", Icon: List },
  { id: "settings", label: "Settings", title: "Settings", Icon: Settings },
];

function today(): string {
  return new Date().toLocaleDateString("en-IN", {
    weekday: "long",
    day: "numeric",
    month: "long",
  });
}

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

function readHash(): { page: PageId; money: MoneyTab; activity: ActivityTab } {
  const [rawPage, rawTab] = window.location.hash.slice(1).split("/");
  const page = NAV.some((item) => item.id === rawPage) ? (rawPage as PageId) : "home";
  const money = ["accounts", "projection", "schedules", "goals", "people"].includes(rawTab)
    ? (rawTab as MoneyTab)
    : "accounts";
  const activity = ["ledger", "imports"].includes(rawTab)
    ? (rawTab as ActivityTab)
    : "ledger";
  return { page, money, activity };
}

export function Shell({ user, onSignedOut }: { user: User | null; onSignedOut: () => void }) {
  const initial = readHash();
  const [page, setPage] = useState<PageId>(initial.page);
  const [moneyTab, setMoneyTab] = useState<MoneyTab>(initial.money);
  const [activityTab, setActivityTab] = useState<ActivityTab>(initial.activity);
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [focusThreadId, setFocusThreadId] = useState<string | null>(null);
  const [assistantOpen, setAssistantOpen] = useState(false);
  const [seed, setSeed] = useState<{ id: number; text: string } | null>(null);
  const [focusSignal, setFocusSignal] = useState(0);
  const [todayOpen, setTodayOpen] = useState(false);
  const [homeVersion, setHomeVersion] = useState(0);
  const [evidence, setEvidence] = useState<ProjectionPayload | null>(null);
  const compact = useCompactViewport();

  function go(next: PageId, sub?: string) {
    setEvidence(null);
    setPage(next);
    const hash = sub ? `${next}/${sub}` : next;
    if (window.location.hash !== `#${hash}`) {
      window.history.pushState(null, "", `#${hash}`);
    }
  }

  useEffect(() => {
    const onPop = () => {
      const next = readHash();
      setPage(next.page);
      setMoneyTab(next.money);
      setActivityTab(next.activity);
      setEvidence(null);
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const refreshUnread = useCallback(async () => {
    try {
      const inbox = await api<NotificationList>("/api/notifications?limit=1");
      setUnread(inbox.unread_count);
    } catch {
      // The badge is best effort; Today shows the queue's own error.
    }
  }, []);

  useEffect(() => {
    const onAsk = (event: Event) => {
      const detail = (event as CustomEvent<string>).detail ?? "";
      setSeed(detail ? { id: Date.now(), text: detail } : null);
      setFocusSignal((value) => value + 1);
      // On Home the conversation is already the page; elsewhere the sheet must open for it.
      if (compact && page !== "home") setAssistantOpen(true);
      setTodayOpen(false);
      setEvidence(null);
    };
    window.addEventListener(ASK_EVENT, onAsk);
    return () => window.removeEventListener(ASK_EVENT, onAsk);
  }, [compact, page]);

  useEffect(() => {
    const onEvidence = (event: Event) => {
      const payload = (event as CustomEvent<ProjectionPayload>).detail;
      if (!payload || !Array.isArray(payload.curve)) return;
      setEvidence(payload);
      setTodayOpen(false);
    };
    window.addEventListener(EVIDENCE_EVENT, onEvidence);
    return () => window.removeEventListener(EVIDENCE_EVENT, onEvidence);
  }, []);

  useEffect(() => {
    const kick = window.setTimeout(() => void refreshUnread(), 0);
    const handle = window.setInterval(() => void refreshUnread(), 30_000);
    return () => {
      window.clearTimeout(kick);
      window.clearInterval(handle);
    };
  }, [refreshUnread]);

  async function signOut() {
    try {
      await logout();
      onSignedOut();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  function ask(text: string) {
    setSeed(text ? { id: Date.now(), text } : null);
    setFocusSignal((value) => value + 1);
    setTodayOpen(false);
    if (compact && page !== "home") setAssistantOpen(true);
  }

  function showEvidence(payload: ProjectionPayload) {
    setEvidence(payload);
    setTodayOpen(false);
  }

  function openChat(threadId: string) {
    setFocusThreadId(threadId);
    setTodayOpen(false);
    if (compact && page !== "home") setAssistantOpen(true);
    go("home");
    void refreshUnread();
  }

  function openActivity(tab: ActivityTab) {
    setTodayOpen(false);
    setActivityTab(tab);
    go("activity", tab);
  }

  function openProjection() {
    setTodayOpen(false);
    setMoneyTab("projection");
    go("money", "projection");
  }

  function openSettings() {
    setTodayOpen(false);
    go("settings", "profile");
  }

  const current = NAV.find((item) => item.id === page) ?? NAV[0];
  const assistant = (
    <Assistant
      seed={seed}
      focusSignal={focusSignal}
      onSeedConsumed={() => setSeed(null)}
      focusThreadId={focusThreadId}
      onFocusHandled={() => setFocusThreadId(null)}
      onClose={compact ? () => setAssistantOpen(false) : undefined}
      onActivity={() => setHomeVersion((value) => value + 1)}
    />
  );

  const renderHomeRail = (onClose?: () => void) => (
    <HomeRail
      onOpenChat={openChat}
      onAsk={ask}
      onOpenActivity={openActivity}
      onInboxChanged={() => void refreshUnread()}
      onOpenProjection={openProjection}
      onOpenEvidence={showEvidence}
      onOpenSettings={openSettings}
      onClose={onClose}
      version={homeVersion}
    />
  );

  const evidencePanel = evidence ? (
    <EvidencePanel
      payload={evidence}
      onClose={() => setEvidence(null)}
      onOpenProjection={() => {
        setEvidence(null);
        openProjection();
      }}
      onAsk={() => {
        setEvidence(null);
        ask("");
      }}
    />
  ) : null;

  const railLabel = evidence
    ? "Sources and assumptions"
    : page === "home"
      ? "Today"
      : "Assistant";

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <Mark size={30} />
          <span className="brand-word">Fin-Agent</span>
          <span className="brand-tag">Private</span>
        </div>

        <nav className="nav-groups" aria-label="Primary">
          {NAV.map(({ id, label, Icon }) => (
            <button
              key={id}
              type="button"
              className={page === id ? "nav-item on" : "nav-item"}
              aria-current={page === id ? "page" : undefined}
              onClick={() => {
                go(id);
                if (id === "home") void refreshUnread();
              }}
            >
              <Icon size={17} strokeWidth={1.7} aria-hidden="true" />
              <span>{label}</span>
              {id === "home" && unread > 0 && (
                <span className="nav-badge" aria-label={`${unread} unread`}>
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="side-foot">
          <span className="privacy-line">
            <ShieldCheck size={13} strokeWidth={1.8} aria-hidden="true" />
            Private instance
          </span>
          <span>{user?.display_name ? `${user.display_name} · ` : ""}Tailscale only</span>
          <span>No analytics</span>
        </div>
      </aside>

      <div className="main">
        <header className={page === "home" ? "topbar topbar-home" : "topbar"}>
          <div className="topbar-title">
            <div className="mobile-brand">
              <Mark size={24} />
              <span>Fin-Agent</span>
            </div>
            <div className="topbar-heading">
              <h1>{current.title}</h1>
              <p className="topbar-date">{today()}</p>
            </div>
          </div>
          <div className="topbar-actions">
            {page === "home" && compact && (
              <Button
                variant="soft"
                color="gray"
                size="2"
                className="topbar-button"
                onClick={() => setTodayOpen(true)}
              >
                <CalendarDays size={15} strokeWidth={1.8} aria-hidden="true" />
                Today
              </Button>
            )}
            {page !== "home" && (
              <Button
                variant="soft"
                color="gray"
                size="2"
                className="topbar-button"
                onClick={() => ask("")}
              >
                <MessageCircle size={15} strokeWidth={1.8} aria-hidden="true" />
                Ask
              </Button>
            )}
            <Button
              variant="ghost"
              color="gray"
              size="2"
              className="topbar-button"
              onClick={() => void signOut()}
            >
              <LogOut size={15} strokeWidth={1.8} aria-hidden="true" />
              Sign out
            </Button>
          </div>
        </header>

        {error && (
          <p className="topbar-error" role="alert">
            <AlertCircle size={15} strokeWidth={1.8} aria-hidden="true" />
            {error}
          </p>
        )}

        <main className={page === "home" ? "content chat-main" : "content"}>
          {page === "home" && (
            <Assistant
              seed={seed}
              focusSignal={focusSignal}
              onSeedConsumed={() => setSeed(null)}
              focusThreadId={focusThreadId}
              onFocusHandled={() => setFocusThreadId(null)}
              onActivity={() => setHomeVersion((value) => value + 1)}
              onEvidence={showEvidence}
            />
          )}
          {page === "money" && (
            <MoneyPage
              tab={moneyTab}
              onTab={(tab) => {
                setMoneyTab(tab);
                go("money", tab);
              }}
            />
          )}
          {page === "activity" && (
            <ActivityPage
              tab={activityTab}
              onTab={(tab) => {
                setActivityTab(tab);
                go("activity", tab);
              }}
            />
          )}
          {page === "settings" && <SettingsPanel />}
        </main>
      </div>

      {!compact && (
        <aside className="context-rail" aria-label={railLabel}>
          <AnimatePresence mode="wait" initial={false}>
            {page === "home" && evidencePanel ? (
              <motion.div
                key="evidence"
                className="rail-fill"
                initial={{ opacity: 0, x: 10 }}
                animate={{ opacity: 1, x: 0 }}
                exit={{ opacity: 0, x: 8 }}
                transition={{ duration: 0.24, ease: [0.22, 1, 0.36, 1] }}
              >
                {evidencePanel}
              </motion.div>
            ) : page === "home" ? (
              <motion.div
                key="today"
                className="rail-fill"
                initial={{ opacity: 0, x: 8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.28, ease: [0.22, 1, 0.36, 1] }}
              >
                {renderHomeRail()}
              </motion.div>
            ) : (
              <div key="assistant" className="rail-fill">
                {assistant}
              </div>
            )}
          </AnimatePresence>
        </aside>
      )}

      {compact && page === "home" && (
        <Dialog.Root open={todayOpen} onOpenChange={setTodayOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay sheet-overlay" />
            <Dialog.Content className="sheet-dialog today-sheet" aria-describedby={undefined}>
              <Dialog.Title className="sr-only">Today</Dialog.Title>
              <div className="sheet-grip" aria-hidden="true" />
              <Theme
                appearance="dark"
                accentColor="blue"
                grayColor="slate"
                radius="large"
                hasBackground={false}
                className="sheet-theme"
              >
                {renderHomeRail(() => setTodayOpen(false))}
              </Theme>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}

      {compact && evidencePanel && (
        <Dialog.Root open onOpenChange={(open) => !open && setEvidence(null)}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay sheet-overlay" />
            <Dialog.Content className="sheet-dialog evidence-sheet" aria-describedby={undefined}>
              <Dialog.Title className="sr-only">Sources and assumptions</Dialog.Title>
              <Theme
                appearance="dark"
                accentColor="blue"
                grayColor="slate"
                radius="large"
                hasBackground={false}
                className="sheet-theme"
              >
                {evidencePanel}
              </Theme>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}

      {compact && page !== "home" && (
        <Dialog.Root open={assistantOpen} onOpenChange={setAssistantOpen}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay sheet-overlay" />
            <Dialog.Content className="sheet-dialog assistant-sheet" aria-describedby={undefined}>
              <Dialog.Title className="sr-only">Assistant</Dialog.Title>
              {assistant}
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}

      <nav className="tabbar" aria-label="Primary">
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className={page === id ? "tabbar-item on" : "tabbar-item"}
            aria-current={page === id ? "page" : undefined}
            aria-label={id === "home" && unread > 0 ? `Home, ${unread} unread` : label}
            onClick={() => {
              go(id);
              if (id === "home") void refreshUnread();
            }}
          >
            <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
            <span>{label}</span>
            {id === "home" && unread > 0 && <span className="tabbar-badge">{unread}</span>}
          </button>
        ))}
      </nav>
    </div>
  );
}
