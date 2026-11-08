import * as Tabs from "@radix-ui/react-tabs";
import { ImportsPanel } from "../panels/Imports";
import { InboxPanel } from "../panels/Inbox";
import { LedgerPanel } from "../panels/Ledger";

export type ActivityTab = "ledger" | "imports" | "inbox";

export function ActivityPage({
  tab,
  importId,
  unread = 0,
  onInboxChanged,
  onTab,
  onImportId,
}: {
  tab: ActivityTab;
  importId?: string | null;
  unread?: number;
  onInboxChanged: () => void;
  onTab: (tab: ActivityTab) => void;
  onImportId?: (importId: string) => void;
}) {
  const tabs: Array<{ id: ActivityTab; label: string }> = [
    { id: "ledger", label: "Ledger" },
    { id: "imports", label: "Imports" },
    { id: "inbox", label: unread > 0 ? `Inbox · ${unread}` : "Inbox" },
  ];
  return (
    <Tabs.Root
      value={tab}
      onValueChange={(value) => onTab(value as ActivityTab)}
      className="workspace"
    >
      <div className="area-head">
        <h2>Activity</h2>
        <Tabs.List className="subtabs" aria-label="Activity">
          {tabs.map((item) => (
            <Tabs.Trigger key={item.id} value={item.id} className="subtab">
              {item.label}
            </Tabs.Trigger>
          ))}
        </Tabs.List>
      </div>
      <Tabs.Content value="ledger" className="workspace-body">
        <LedgerPanel />
      </Tabs.Content>
      <Tabs.Content value="imports" className="workspace-body">
        <ImportsPanel importId={importId} onImportId={onImportId} />
      </Tabs.Content>
      <Tabs.Content value="inbox" className="workspace-body">
        <InboxPanel onChanged={onInboxChanged} />
      </Tabs.Content>
    </Tabs.Root>
  );
}
