import { useCallback, useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Home, List, MessageSquare, Settings, Wallet } from "lucide-react";
import { api, errorMessage } from "./api";
import { ASK_EVENT } from "./ask";
import { logout, type User } from "./auth";
import { Assistant } from "./chat/Chat";
import { ActivityPage, type ActivityTab } from "./pages/Activity";
import { HomeRail } from "./pages/Home";
import { MoneyPage, type MoneyTab } from "./pages/Money";
import { SettingsPanel } from "./panels/Settings";
import type { NotificationList } from "./types";

type PageId = "home" | "money" | "activity" | "settings";

const NAV: Array<{
  id: PageId;
  label: string;
  title: string;
  Icon: typeof Home;
}> = [
  { id: "home", label: "Home", title: "Today", Icon: Home },
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
  const compact = useCompactViewport();

  function go(next: PageId, sub?: string) {
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
    };
    window.addEventListener("popstate", onPop);
    return () => window.removeEventListener("popstate", onPop);
  }, []);

  const refreshUnread = useCallback(async () => {
    try {
      const inbox = await api<NotificationList>("/api/notifications?limit=1");
      setUnread(inbox.unread_count);
    } catch {
      // The badge is best effort; Home shows the queue's own error.
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
    };
    window.addEventListener(ASK_EVENT, onAsk);
    return () => window.removeEventListener(ASK_EVENT, onAsk);
  }, [compact, page]);

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

  const homeRail = (
    <HomeRail
      onOpenChat={openChat}
      onAsk={ask}
      onOpenActivity={openActivity}
      onInboxChanged={() => void refreshUnread()}
      onOpenProjection={openProjection}
      version={homeVersion}
    />
  );

  return (
    <div className="app">
      <aside className="sidebar">
        <div className="brand">
          <span className="brand-mark" aria-hidden="true">
            <MessageSquare size={14} strokeWidth={2.3} />
          </span>
          Fin-Agent
          <em>Private</em>
        </div>

        <nav className="nav-groups" aria-label="Areas">
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
              {label}
              {id === "home" && unread > 0 && (
                <span className="nav-badge" aria-label={`${unread} unread`}>
                  {unread > 99 ? "99+" : unread}
                </span>
              )}
            </button>
          ))}
        </nav>

        <div className="side-foot">
          <b>Private instance</b>
          {user?.display_name ? `${user.display_name} · ` : ""}Tailscale only · no analytics
        </div>
      </aside>

      <div className="main">
        <header className={page === "home" ? "topbar topbar-home" : "topbar"}>
          <div className="topbar-title">
            <div className="mobile-brand">
              <span className="brand-mark" aria-hidden="true">
                <MessageSquare size={14} strokeWidth={2.3} />
              </span>
              Fin-Agent
            </div>
            {page === "home" ? (
              <>
                <h1 className="sr-only">Ask Fin-Agent</h1>
                <p className="topbar-date">{today()}</p>
              </>
            ) : (
              <div>
                <h1>{current.title}</h1>
                <p className="page-sub">{today()}</p>
              </div>
            )}
          </div>
          <div className="topbar-actions">
            {page === "home" && compact && (
              <button type="button" className="ghost" onClick={() => setTodayOpen(true)}>
                Today
              </button>
            )}
            {page !== "home" && (
              <button type="button" className="ghost ask-btn" onClick={() => ask("")}>
                Ask
              </button>
            )}
            <button type="button" className="ghost" onClick={() => void signOut()}>
              Sign out
            </button>
          </div>
        </header>

        {error && (
          <p className="error" role="alert" style={{ padding: "0 32px" }}>
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

      {compact ? (
        page === "home" ? (
          <Dialog.Root open={todayOpen} onOpenChange={setTodayOpen}>
            <Dialog.Portal>
              <Dialog.Overlay className="dialog-overlay" />
              <Dialog.Content className="today-dialog" aria-describedby={undefined}>
                <div className="today-dialog-head">
                  <Dialog.Title>Today</Dialog.Title>
                  <Dialog.Close asChild>
                    <button type="button" className="ghost small">
                      Close
                    </button>
                  </Dialog.Close>
                </div>
                {homeRail}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        ) : (
          <Dialog.Root open={assistantOpen} onOpenChange={setAssistantOpen}>
            <Dialog.Portal>
              <Dialog.Overlay className="dialog-overlay" />
              <Dialog.Content className="assistant-dialog" aria-describedby={undefined}>
                <Dialog.Title className="sr-only">Assistant</Dialog.Title>
                {assistant}
              </Dialog.Content>
            </Dialog.Portal>
          </Dialog.Root>
        )
      ) : page === "home" ? (
        <aside className="home-rail" aria-label="Today">
          {homeRail}
        </aside>
      ) : (
        <aside className="assistant-rail" aria-label="Assistant">
          {assistant}
        </aside>
      )}

      <nav className="tabbar" aria-label="Primary">
        {NAV.map(({ id, label, Icon }) => (
          <button
            key={id}
            type="button"
            className={page === id ? "tabbar-item on" : "tabbar-item"}
            aria-current={page === id ? "page" : undefined}
            onClick={() => go(id)}
          >
            <Icon size={20} strokeWidth={1.8} aria-hidden="true" />
            {label}
            {id === "home" && unread > 0 && <span className="tabbar-badge">{unread}</span>}
          </button>
        ))}
      </nav>
    </div>
  );
}
