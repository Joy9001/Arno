import * as Tabs from "@radix-ui/react-tabs";
import { AccountsPanel } from "../panels/Accounts";
import { GoalsPanel } from "../panels/Goals";
import { PeoplePanel } from "../panels/People";
import { ProjectionPanel } from "../panels/Projection";
import { SchedulesPanel } from "../panels/Schedules";

export type MoneyTab = "accounts" | "people" | "goals" | "schedules" | "projection";

const TABS: Array<{ id: MoneyTab; label: string }> = [
  { id: "accounts", label: "Accounts" },
  { id: "projection", label: "Projection" },
  { id: "schedules", label: "Schedules" },
  { id: "goals", label: "Goals" },
  { id: "people", label: "People" },
];

export function MoneyPage({ tab, onTab }: { tab: MoneyTab; onTab: (tab: MoneyTab) => void }) {
  return (
    <Tabs.Root
      value={tab}
      onValueChange={(value) => onTab(value as MoneyTab)}
      className="workspace"
    >
      <Tabs.List className="subtabs" aria-label="Money">
        {TABS.map((item) => (
          <Tabs.Trigger key={item.id} value={item.id} className="subtab">
            {item.label}
          </Tabs.Trigger>
        ))}
      </Tabs.List>
      <Tabs.Content value="accounts" className="workspace-body">
        <AccountsPanel />
      </Tabs.Content>
      <Tabs.Content value="people" className="workspace-body">
        <PeoplePanel />
      </Tabs.Content>
      <Tabs.Content value="goals" className="workspace-body">
        <GoalsPanel />
      </Tabs.Content>
      <Tabs.Content value="schedules" className="workspace-body">
        <SchedulesPanel />
      </Tabs.Content>
      <Tabs.Content value="projection" className="workspace-body">
        <ProjectionPanel />
      </Tabs.Content>
    </Tabs.Root>
  );
}
