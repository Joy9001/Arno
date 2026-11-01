import { formatDate, formatPaise, percent } from "../format";
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
        <span className={`badge ${goal.status === "active" ? "good" : "muted"}`}>{goal.status}</span>
      </div>
      <div className="progress" aria-label={`${goal.name} progress`}>
        <span style={{ width }} />
      </div>
      <div className="goal-numbers">
        <strong>{formatPaise(goal.saved_paise)}</strong>
        <span className="muted">of {formatPaise(goal.target_paise)}</span>
        <span className={`badge ${goal.progress >= 1 ? "good" : "muted"}`}>{percent(goal.progress)}</span>
      </div>
      <p className="fine">
        {goal.remaining_paise === 0
          ? "Fully funded. The money is fenced in its own goal account."
          : `${formatPaise(goal.remaining_paise)} still to save.`}
      </p>
    </article>
  );
}

export function GoalsPanel() {
  const { data, error, loading } = useApi<Goals>("/api/goals");

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Goals</p>
          <h2>Money that is already spoken for</h2>
          <p className="muted">
            Each goal has its own non-liquid ledger account, so its savings are real money and
            never spendable by the projection.
          </p>
        </div>
        {data && <p className="fine">as of {data.as_of}</p>}
      </header>

      {loading && !data && <p className="muted">Loading goals…</p>}
      {error && <p className="error">{error}</p>}
      {data && data.goals.length === 0 && <p className="muted">No goals yet.</p>}
      <div className="goal-grid">
        {data?.goals.map((goal) => <GoalCard key={goal.id} goal={goal} />)}
      </div>
    </section>
  );
}
