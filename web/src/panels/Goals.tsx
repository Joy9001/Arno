import { useState } from "react";
import { formatDate, formatPaise, percent } from "../format";
import type { Goal } from "../types";
import { useApi } from "../useApi";
import {
  MoneyAsk,
  MoneyEmpty,
  MoneyError,
  MoneyPageHeader,
  MoneyPageSkeleton,
  MoneyPager,
  MoneyRefreshing,
  MoneySection,
  MoneySummary,
} from "./MoneyBits";
import { countLabel, pageSlice, safePage } from "./moneyView";

type Goals = { goals: Goal[]; as_of: string };
const PAGE_SIZE = 8;
const GOALS_DESCRIPTION =
  "Each goal has its own non-liquid ledger account, so its savings are real money and never spendable by the projection.";

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  achieved: "Achieved",
  paused: "Paused",
  abandoned: "Abandoned",
};

function GoalRow({ goal }: { goal: Goal }) {
  const hasTarget = goal.target_paise > 0;
  const progress = hasTarget ? Math.min(Math.max(goal.progress, 0), 1) : 0;
  const deadline = goal.deadline
    ? `Due ${formatDate(goal.deadline)}`
    : "No deadline set";
  const remaining = !hasTarget
    ? "No target set"
    : goal.remaining_paise === 0
      ? "Fully funded. The money is fenced in its own goal account."
      : `${formatPaise(goal.remaining_paise)} still to save.`;

  return (
    <article className="moneyv-goal">
      <div className="moneyv-goal-top">
        <div className="moneyv-goal-title">
          <strong>{goal.name}</strong>
          {goal.status !== "active" && (
            <span className={goal.status === "achieved" ? "moneyv-chip good" : "moneyv-chip warn"}>
              {STATUS_LABEL[goal.status] ?? goal.status}
            </span>
          )}
        </div>
        <div className="moneyv-goal-figures">
          <strong>{formatPaise(goal.saved_paise)}</strong>
          <span>of {hasTarget ? formatPaise(goal.target_paise) : "no target"}</span>
          <span className={progress >= 1 ? "moneyv-chip good" : "moneyv-chip"}>
            {hasTarget ? percent(goal.progress) : "—"}
          </span>
        </div>
      </div>
      <div
        className={progress >= 1 ? "moneyv-progress done" : "moneyv-progress"}
        role="progressbar"
        aria-label={`${goal.name} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
        aria-valuetext={hasTarget ? percent(goal.progress) : "No target"}
      >
        <span style={{ width: `${progress * 100}%` }} />
      </div>
      <div className="moneyv-goal-foot">
        <span className="moneyv-meta">
          {deadline} · Priority {goal.priority} of 5 · {remaining}
        </span>
        <MoneyAsk prompt={`How is ${goal.name} tracking?`} />
      </div>
    </article>
  );
}

export function GoalsPanel() {
  const { data, error, loading, reload } = useApi<Goals>("/api/goals");
  const [activePage, setActivePage] = useState(0);
  const [otherPage, setOtherPage] = useState(0);

  if (loading && !data) {
    return (
      <MoneyPageSkeleton
        title="Goals"
        description={GOALS_DESCRIPTION}
        rows={4}
      />
    );
  }

  const header = (
    <MoneyPageHeader
      title="Goals"
      description={GOALS_DESCRIPTION}
      aside={
        data ? (
          <>
            As of {formatDate(data.as_of)}
            {loading && <MoneyRefreshing />}
          </>
        ) : undefined
      }
    />
  );

  if (!data) {
    return (
      <section className="panel">
        {header}
        <MoneyError
          title="Couldn’t load goals"
          message={error ?? "The goals request did not return data."}
          onRetry={reload}
        />
      </section>
    );
  }

  const goals = data.goals;
  const active = goals.filter((goal) => goal.status === "active");
  const other = goals.filter((goal) => goal.status !== "active");
  const saved = active.reduce((total, goal) => total + goal.saved_paise, 0);
  const target = active.reduce((total, goal) => total + goal.target_paise, 0);
  const nextDeadline = active
    .map((goal) => goal.deadline)
    .filter((deadline): deadline is string => Boolean(deadline))
    .sort()[0];
  const activePageSafe = safePage(activePage, active.length, PAGE_SIZE);
  const otherPageSafe = safePage(otherPage, other.length, PAGE_SIZE);

  return (
    <section className="panel">
      {header}
      {error && <MoneyError title="Couldn’t refresh goals" message={error} onRetry={reload} />}

      {active.length > 0 && (
        <MoneySummary
          label="Active goal summary"
          cells={[
            {
              label: "Active goals",
              value: countLabel(active.length, "goal"),
              note: other.length
                ? `${countLabel(other.length, "other goal")} below`
                : "All goals active",
            },
            {
              label: "Saved",
              value: formatPaise(saved),
              note: "Across active goals",
            },
            {
              label: "Target",
              value: formatPaise(target),
              note: `${percent(target > 0 ? saved / target : 0)} of the active target`,
            },
            {
              label: "Next deadline",
              value: nextDeadline ? formatDate(nextDeadline) : "—",
              note: nextDeadline ? "Among active goals" : "No active deadline",
            },
          ]}
        />
      )}

      <MoneySection title="Active goals" note={active.length ? countLabel(active.length, "goal") : undefined}>
        {active.length === 0 ? (
          <MoneyEmpty prompt="Add a goal: " action="Ask to add one">
            No active goals yet.
          </MoneyEmpty>
        ) : (
          <>
            <div className="moneyv-list">
              {pageSlice(active, activePageSafe, PAGE_SIZE).map((goal) => (
                <GoalRow key={goal.id} goal={goal} />
              ))}
            </div>
            <MoneyPager
              page={activePageSafe}
              pageSize={PAGE_SIZE}
              total={active.length}
              noun="goals"
              onPage={setActivePage}
            />
          </>
        )}
      </MoneySection>

      {other.length > 0 && (
        <MoneySection title="Other goals" note={countLabel(other.length, "goal")}>
          <div className="moneyv-list">
            {pageSlice(other, otherPageSafe, PAGE_SIZE).map((goal) => (
              <GoalRow key={goal.id} goal={goal} />
            ))}
          </div>
          <MoneyPager
            page={otherPageSafe}
            pageSize={PAGE_SIZE}
            total={other.length}
            noun="goals"
            onPage={setOtherPage}
          />
        </MoneySection>
      )}
    </section>
  );
}
