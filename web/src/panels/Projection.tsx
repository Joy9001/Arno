import { useMemo, useState, type FormEvent } from "react";
import { AffordabilityLab } from "../chat/widgets";
import { parseProjectionReceipt } from "../projectionReceipt";
import { useApi } from "../useApi";
import { MoneyError, MoneyPageHeader, MoneyPageSkeleton, MoneyRefreshing } from "./MoneyBits";

export function ProjectionPanel() {
  const [draft, setDraft] = useState("90");
  const [horizon, setHorizon] = useState(90);
  const [formError, setFormError] = useState<string | null>(null);
  const response = useApi<unknown>(`/api/projection?horizon_days=${horizon}`);
  const data = useMemo(() => parseProjectionReceipt(response.data), [response.data]);
  const error = response.error ?? (response.data != null && !data ? "The projection response could not be read." : null);

  function changeHorizon(event: FormEvent) {
    event.preventDefault();
    const value = Number(draft);
    if (!Number.isInteger(value) || value < 90 || value > 3650) {
      setFormError("Choose between 90 and 3,650 days.");
      return;
    }
    setFormError(null);
    setHorizon(value);
  }

  return <div className="moneyv">
    <MoneyPageHeader title="Timeline" description="Past spending and the range of possible balances ahead." aside={response.loading && data ? <MoneyRefreshing /> : undefined} />
    <form onSubmit={changeHorizon} className="moneyv-horizon">
      <label>Projection horizon (days) <input type="number" min="90" max="3650" value={draft} onChange={(event) => setDraft(event.target.value)} /></label>
      <button type="submit" className="ghost small">Update</button>
      {formError && <p role="alert">{formError}</p>}
    </form>
    {error && <MoneyError title="Projection unavailable" message={error} onRetry={response.reload} />}
    {!data && response.loading && <MoneyPageSkeleton title="Timeline" description="Loading projection?" />}
    {data && <AffordabilityLab key={horizon} seed={data} initialTimeline />}
  </div>;
}
