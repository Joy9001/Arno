import { motion, useReducedMotion } from "motion/react";
import { formatDate, formatPaise } from "../../format";
import type { ProjectionPayload } from "../../projection";
import { projectionReadiness } from "../../projectionData";

export function GoalProgressRing({ payload }: { payload: ProjectionPayload }) {
  const reduced = useReducedMotion();
  const ready = projectionReadiness(payload).ready;
  if (!payload.goals?.length) return null;
  return <section className="chat-card" aria-label="Goal progress">
    <h3>Goals</h3>
    {payload.goals.map((goal) => {
      const target = goal.target?.paise;
      const saved = goal.saved?.paise;
      const ratio = target != null && target > 0 && saved != null ? Math.min(1, Math.max(0, saved / target)) : null;
      return <div key={goal.id} className="flex items-center gap-4 py-2">
        <svg width="64" height="64" viewBox="0 0 64 64" aria-hidden="true">
          <circle cx="32" cy="32" r="27" fill="none" stroke="var(--surface-3)" strokeWidth="6" />
          {ratio != null && <motion.circle cx="32" cy="32" r="27" fill="none" stroke="var(--lime)" strokeWidth="6" pathLength="1" strokeDasharray="1" initial={{ strokeDashoffset: reduced ? 1 - ratio : 1 }} animate={{ strokeDashoffset: 1 - ratio }} transform="rotate(-90 32 32)" />}
        </svg>
        <div><strong>{goal.name}</strong><p>{saved == null ? "Saved amount unknown" : formatPaise(saved)}{target == null ? " · target unknown" : ` of ${formatPaise(target)}`}</p>
          <p>{!ready ? "Projection unavailable until balances are known" : goal.reached ? "Reached" : goal.completes_on ? `Projected ${formatDate(goal.completes_on)}` : "No projected completion date"}</p></div>
      </div>;
    })}
  </section>;
}
