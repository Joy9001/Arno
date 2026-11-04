import { MessageCircle } from "lucide-react";
import { askAgent } from "../ask";
import { formatDate, formatPaise, percent } from "../format";
import { Loading } from "../Loading";
import type { Goal } from "../types";
import { useApi } from "../useApi";
type Goals = { goals: Goal[]; as_of: string };

function GoalCard({ goal }: { goal: Goal }) {
  const width = `${Math.min(Math.max(goal.progress, 0), 1) * 100}%`;
  return (
    <article className="goal">
      <div className="goal-head">
        <div>
          <h3>{goal.name}</h3>
          <p className="fine">
            {goal.deadline ? `Due ${formatDate(goal.deadline)}` : "No deadline"} · priority{" "}
            {goal.priority}
          </p>
        </div>
        <span className={`badge ${goal.status === "active" ? "muted" : "good"}`}>
          {goal.status === "active" ? "Active" : goal.status}
        </span>
      </div>
      <div className="progress" aria-label={`${goal.name} progress`}>
        <span style={{ width }} />
      </div>
      <div className="goal-numbers">
        <strong>{formatPaise(goal.saved_paise)}</strong>
        <span className="muted">of {formatPaise(goal.target_paise)}</span>
        <span className={`badge ${goal.progress >= 1 ? "good" : "muted"}`}>
          {percent(goal.progress)}
        </span>
      </div>
      <p className="fine">
        {goal.remaining_paise === 0
          ? "Fully funded. The money is fenced in its own goal account."
          : `${formatPaise(goal.remaining_paise)} still to save.`}
      </p>
      <button
        type="button"
        className="ask-link"
        onClick={() => askAgent(`How is ${goal.name} tracking?`)}
      >
        <MessageCircle size={13} aria-hidden="true" />
        Ask
      </button>
    </article>
  );
}

export function GoalsPanel() {
  const { data, error, loading, reload } = useApi<Goals>("/api/goals");
  const goals = data?.goals ?? [];
  const saved = goals.reduce((total, goal) => total + goal.saved_paise, 0);
  const target = goals.reduce((total, goal) => total + goal.target_paise, 0);
  const nextDeadline = goals
    .map((goal) => goal.deadline)
    .filter((deadline): deadline is string => Boolean(deadline))
    .sort()[0];

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <h2>Money that is already spoken for</h2>
          <p className="muted">
            Each goal has its own non-liquid ledger account, so its savings are real money and never
            spendable by the projection.
          </p>
        </div>
        {data && <p className="fine">as of {data.as_of}</p>}
      </header>

      {goals.length > 0 && (
        <div className="summary-strip">
          <div className="summary-cell">
            <span>Saved</span>
            <strong>{formatPaise(saved)}</strong>
          </div>
          <div className="summary-cell">
            <span>Target</span>
            <strong>{formatPaise(target)}</strong>
          </div>
          <div className="summary-cell">
            <span>Next deadline</span>
            <strong>{nextDeadline ? formatDate(nextDeadline) : "—"}</strong>
          </div>
        </div>
      )}

      {loading && !data && <Loading label="Loading goals…" />}
      {error && (
        <div className="panel-error">
          <p className="error">{error}</p>
          <button type="button" className="ghost small" onClick={reload}>
            Retry
          </button>
        </div>
      )}
      {data && goals.length === 0 && (
        <div className="empty-state">
          <p>No goals yet.</p>
          <button type="button" className="ask-link" onClick={() => askAgent("Add a goal: ")}>
            <MessageCircle size={13} aria-hidden="true" />
            Ask to add one
          </button>
        </div>
      )}
      <div className="goal-grid">
        {goals.map((goal) => (
          <GoalCard key={goal.id} goal={goal} />
        ))}
      </div>
    </section>
  );
}
