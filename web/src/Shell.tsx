import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { logout, type Health, type User } from "./auth";
import { ChatHome } from "./chat/Chat";
import { AccountsPanel } from "./panels/Accounts";
import { GoalsPanel } from "./panels/Goals";
import { ImportsPanel } from "./panels/Imports";
import { LedgerPanel } from "./panels/Ledger";
import { PeoplePanel } from "./panels/People";
import { SchedulesPanel } from "./panels/Schedules";
import { SettingsPanel } from "./panels/Settings";

const TABS = [
  { id: "chat", label: "Chat" },
  { id: "ledger", label: "Ledger" },
  { id: "imports", label: "Imports" },
  { id: "accounts", label: "Accounts and cards" },
  { id: "schedules", label: "Schedules" },
  { id: "goals", label: "Goals" },
  { id: "people", label: "People" },
  { id: "settings", label: "Settings" },
] as const;

type TabId = (typeof TABS)[number]["id"];

function Panel({ tab }: { tab: TabId }) {
  switch (tab) {
    case "chat":
      return <ChatHome />;
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

  async function signOut() {
    try {
      await logout();
      onSignedOut();
    } catch (caught) {
      setError(errorMessage(caught));
    }
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
            onClick={() => setTab(item.id)}
          >
            {item.label}
          </button>
        ))}
      </nav>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      <main className={tab === "chat" ? "content chat-content" : "content"}>
        <Panel tab={tab} />
      </main>
    </div>
  );
}
