import { formatMoment } from "../format";
import type { Preference } from "../chat/types";

/** A memory that replaced an earlier value shows what it said and when it changed. */
export function MemoryPrevious({ previous }: { previous: Preference["previous"] }) {
  if (!previous) return null;
  return (
    <p className="stg-list-meta">
      Previously: {previous.text} · changed {formatMoment(previous.superseded_at)}
    </p>
  );
}
