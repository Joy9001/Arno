import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { logout, type Health, type User } from "./auth";
import { ChatHome } from "./chat/Chat";
import { AccountsPanel } from "./panels/Accounts";
import { GoalsPanel } from "./panels/Goals";
import { ImportsPanel } from "./panels/Imports";
import { InboxPanel } from "./panels/Inbox";
import { LedgerPanel } from "./panels/Ledger";
import { PeoplePanel } from "./panels/People";
import { ProjectionPanel } from "./panels/Projection";
import { SchedulesPanel } from "./panels/Schedules";
import { SettingsPanel } from "./panels/Settings";
import type { NotificationList } from "./types";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "inbox", label: "Inbox" },
  { id: "ledger", label: "Ledger" },
  { id: "imports", label: "Imports" },
  { id: "accounts", label: "Accounts and cards" },
  { id: "schedules", label: "Schedules" },
  { id: "goals", label: "Goals" },
  { id: "projection", label: "Projection" },
  { id: "people", label: "People" },
  { id: "settings", label: "Settings" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function Panel({
  tab,
  focusThreadId,
  onThreadFocused,
  onOpenChat,
  onInboxChanged,
}: {
  tab: TabId;
  focusThreadId: string | null;
  onThreadFocused: () => void;
  onOpenChat: (threadId: string) => void;
  onInboxChanged: () => void;
}) {
  switch (tab) {
    case "chat":
      return <ChatHome focusThreadId={focusThreadId} onFocusHandled={onThreadFocused} />;
    case "inbox":
      return <InboxPanel onOpenChat={onOpenChat} onChanged={onInboxChanged} />;
    case "ledger":
      return <LedgerPanel />;
    case "imports":
      return <ImportsPanel />;
    case "accounts":
      return <AccountsPanel />;
    case "schedules":
      return <SchedulesPanel />;
    case "goals":
      return <GoalsPanel />;
    case "projection":
      return <ProjectionPanel />;
    case "people":
      return <PeoplePanel />;
    case "settings":
      return <SettingsPanel />;
  }
}

export function Shell({ user, onSignedOut }: { user: User | null; onSignedOut: () => void }) {
  const [tab, setTab] = useState<TabId>("chat");
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [unread, setUnread] = useState(0);
  const [focusThreadId, setFocusThreadId] = useState<string | null>(null);

  const refreshUnread = useCallback(async () => {
    try {
      const inbox = await api<NotificationList>("/api/notifications?limit=1");
      setUnread(inbox.unread_count);
    } catch {
      // The badge is best effort; the Inbox panel shows its own error.
    }
  }, []);

  useEffect(() => {
    let active = true;
    api<Health>("/api/health")
      .then((result) => {
        if (active) setHealth(result);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
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

  function openChat(threadId: string) {
    setFocusThreadId(threadId);
    setTab("chat");
    void refreshUnread();
  }

  return (
    <div className="app wide">
      <header className="topbar">
        <div>
          <p className="eyebrow">Fin-Agent</p>
          <h1>{user?.display_name ?? "Fin-Agent"}</h1>
        </div>
        <div className="topbar-actions">
          {health && <span className="fine">schema v{health.schema_version}</span>}
          <button type="button" className="ghost" onClick={() => void signOut()}>
            Sign out
          </button>
        </div>
      </header>

      <nav className="tabs" aria-label="Panels">
        {TABS.map((item) => (
          <button
            key={item.id}
            type="button"
            className={tab === item.id ? "tab active" : "tab"}
            aria-current={tab === item.id ? "page" : undefined}
            onClick={() => {
              setTab(item.id);
              if (item.id === "inbox") void refreshUnread();
            }}
          >
            {item.label}
            {item.id === "inbox" && unread > 0 && (
              <span className="tab-count" aria-label={`${unread} unread`}>
                {unread > 99 ? "99+" : unread}
              </span>
            )}
          </button>
        ))}
      </nav>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <main className={tab === "chat" ? "content chat-content" : "content"}>
        <Panel
          tab={tab}
          focusThreadId={focusThreadId}
          onThreadFocused={() => setFocusThreadId(null)}
          onOpenChat={openChat}
          onInboxChanged={() => void refreshUnread()}
        />
      </main>
    </div>
  );
}
