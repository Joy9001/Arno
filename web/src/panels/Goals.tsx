import { useState } from "react";
import { Orb } from "../Orb";
import type { ProjectionGoal, ProjectionPayload } from "../projection";
import { projectionReadiness } from "../projectionData";
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
} from "./MoneyBits";
import { countLabel, pageSlice, safePage } from "./moneyView";

type Goals = { goals: Goal[]; as_of: string };
const PAGE_SIZE = 8;
const GOALS_DESCRIPTION =
  "Savings fenced in goal accounts, with dates from your current plan.";

const STATUS_LABEL: Record<string, string> = {
  active: "Active",
  achieved: "Achieved",
  paused: "Paused",
  abandoned: "Abandoned",
};

function GoalRow({ goal, forecast }: { goal: Goal; forecast?: ProjectionGoal }) {
  const hasTarget = goal.target_paise > 0;
  const progress = hasTarget ? Math.min(Math.max(goal.progress, 0), 1) : 0;
  const remaining = !hasTarget
    ? "No target set"
    : goal.remaining_paise === 0
      ? "Fully funded. The money is fenced in its own goal account."
      : `${formatPaise(goal.remaining_paise)} still to save.`;

  return (
    <article className="moneyv-goal">
      <div className="goal-identity">
        <span className="moneyv-chip">Priority {goal.priority} of 5</span>
        {forecast?.slips_days != null && forecast.slips_days > 0 && (
          <span className="moneyv-chip warn">{forecast.slips_days} days past deadline</span>
        )}
      </div>
      <div className="moneyv-goal-top">
        <div className="moneyv-goal-title">
          <strong>{goal.name}</strong>
          {goal.status !== "active" && (
            <span className={goal.status === "achieved" ? "moneyv-chip good" : "moneyv-chip warn"}>
              {STATUS_LABEL[goal.status] ?? goal.status}
            </span>
          )}
        </div>
      </div>
      <div
        className={`goal-ring${progress >= 1 ? " done" : ""}${forecast?.slips_days && forecast.slips_days > 0 ? " late" : ""}`}
        role="progressbar"
        aria-label={`${goal.name} progress`}
        aria-valuemin={0}
        aria-valuemax={100}
        aria-valuenow={Math.round(progress * 100)}
        aria-valuetext={hasTarget ? percent(goal.progress) : "No target"}
      >
        <svg viewBox="0 0 120 120" aria-hidden="true">
          <circle className="goal-ring-track" cx="60" cy="60" r="50" />
          <circle className="goal-ring-fill" cx="60" cy="60" r="50" pathLength="100" strokeDasharray={`${progress * 100} 100`} />
        </svg>
        <strong>{hasTarget ? percent(goal.progress) : "—"}</strong>
      </div>
      <div className="moneyv-goal-figures">
        <strong>{formatPaise(goal.saved_paise)}</strong>
        <span>of {hasTarget ? formatPaise(goal.target_paise) : "no target"}</span>
      </div>
      <dl className="goal-dates">
        <div><dt>Projected completion</dt><dd>{forecast?.completes_on ? formatDate(forecast.completes_on) : forecast ? "Beyond forecast" : "Unavailable"}</dd></div>
        <div><dt>Deadline</dt><dd>{goal.deadline ? formatDate(goal.deadline) : "Not set"}</dd></div>
      </dl>
      <div className="moneyv-goal-foot">
        <span className="moneyv-meta">{remaining}</span>
        <MoneyAsk prompt={`How is ${goal.name} tracking?`} />
      </div>
    </article>
  );
}

export function GoalsPanel() {
  const { data, error, loading, reload } = useApi<Goals>("/api/goals");
  const projection = useApi<ProjectionPayload>("/api/projection?horizon_days=365");
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
  const activePageSafe = safePage(activePage, active.length, PAGE_SIZE);
  const otherPageSafe = safePage(otherPage, other.length, PAGE_SIZE);
  const forecastReady = projection.data && projectionReadiness(projection.data).ready;
  const forecasts = forecastReady ? projection.data?.goals ?? [] : [];
  const slipping = forecasts.find((goal) => active.some((item) => item.id === goal.id) && goal.slips_days != null && goal.slips_days > 0);

  return (
    <section className="panel">
      {header}
      {error && <MoneyError title="Couldn’t refresh goals" message={error} onRetry={reload} />}
      {slipping && (
        <div className="agent-note">
          <Orb size={30} />
          <p><strong>{slipping.name}</strong> is projected to land {slipping.slips_days} days past its deadline.</p>
          <MoneyAsk prompt={`What would bring ${slipping.name} back to its deadline?`} label="Explore the tradeoff" />
        </div>
      )}

      <MoneySection title="Active goals" note={active.length ? countLabel(active.length, "goal") : undefined}>
        {active.length === 0 ? (
          <MoneyEmpty prompt="Add a goal: " action="Ask to add one">
            No active goals yet.
          </MoneyEmpty>
        ) : (
          <>
            <div className="goal-grid">
              {pageSlice(active, activePageSafe, PAGE_SIZE).map((goal) => (
                <GoalRow key={goal.id} goal={goal} forecast={forecasts.find((item) => item.id === goal.id)} />
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
          <div className="goal-grid">
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
      {active.length > 0 && (
        <MoneySection title="When each goal lands" note="365-day projection">
          {projection.error ? (
            <MoneyError title="Couldn’t load goal dates" message={projection.error} onRetry={projection.reload} />
          ) : !projection.data ? (
            <p className="moneyv-meta" role="status">Computing goal dates…</p>
          ) : !forecastReady ? (
            <p className="moneyv-meta">{projectionReadiness(projection.data).message}</p>
          ) : (
            <div className="goal-landings">
              {pageSlice(active, activePageSafe, PAGE_SIZE).map((goal) => {
                const forecast = forecasts.find((item) => item.id === goal.id);
                const completion = forecast?.completes_on;
                const days = completion ? Math.max(0, (Date.parse(completion) - Date.parse(projection.data!.as_of)) / 86_400_000) : null;
                return (
                  <div className="goal-landing" key={goal.id}>
                    <strong>{goal.name}</strong>
                    <div className="goal-landing-track" aria-hidden="true">
                      {days !== null && <span style={{ width: `${Math.max(1, Math.min(100, days / 365 * 100))}%` }} />}
                    </div>
                    <span>{completion ? formatDate(completion) : forecast ? "Beyond forecast" : "Unavailable"}</span>
                  </div>
                );
              })}
              <p className="moneyv-meta">Projected dates from {formatDate(projection.data.as_of)}, using your current plan. Dates beyond the horizon remain unknown.</p>
            </div>
          )}
        </MoneySection>
      )}
    </section>
  );
}
