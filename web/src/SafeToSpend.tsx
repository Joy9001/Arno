import { displayText } from "./chat/display-text";
import { formatDate, formatPaise } from "./format";
import type { ProjectionPayload } from "./projection";
import { dataQualityNotes, safeToSpendAmount } from "./projectionData";

/** The safe-to-spend line; `showNotes` stays on only where no `DataQuality` list exists. */
export function SafeToSpend({
  payload,
  className,
  showNotes = true,
}: {
  payload: ProjectionPayload;
  className?: string;
  showNotes?: boolean;
}) {
  const amount = safeToSpendAmount(payload);
  const safe = payload.safe_to_spend;
  if (amount === null || !safe) return null;
  const notes = showNotes ? dataQualityNotes(payload) : [];
  return (
    <>
      <p className={className}>
        {formatPaise(amount)} safe to spend today · as of {formatDate(payload.as_of)} ·{" "}
        {safe.binding === "floor" ? "Floor" : "Obligation"} binds on {formatDate(safe.binding_date)}.
      </p>
      {notes.length > 0 && (
        <ul className="projection-list">
          {notes.map((note) => (
            <li key={note}>{displayText(note)}</li>
          ))}
        </ul>
      )}
    </>
  );
}
