import * as Tabs from "@radix-ui/react-tabs";
import { ImportsPanel } from "../panels/Imports";
import { LedgerPanel } from "../panels/Ledger";

export type ActivityTab = "ledger" | "imports";

const TABS: Array<{ id: ActivityTab; label: string }> = [
  { id: "ledger", label: "Ledger" },
  { id: "imports", label: "Imports" },
];

export function ActivityPage({
  tab,
  onTab,
}: {
  tab: ActivityTab;
  onTab: (tab: ActivityTab) => void;
}) {
  return (
    <Tabs.Root
      value={tab}
      onValueChange={(value) => onTab(value as ActivityTab)}
      className="workspace"
    >
      <Tabs.List className="subtabs" aria-label="Activity">
        {TABS.map((item) => (
          <Tabs.Trigger key={item.id} value={item.id} className="subtab">
            {item.label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      <Tabs.Content value="ledger" className="workspace-body">
        <LedgerPanel />
      </Tabs.Content>
      <Tabs.Content value="imports" className="workspace-body">
        <ImportsPanel />
      </Tabs.Content>
    </Tabs.Root>
  );
}
