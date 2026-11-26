import type { ReactNode } from "react";
import { ChevronLeft, ChevronRight } from "lucide-react";

export function ActivityPager({ label, info, previousDisabled, nextDisabled, onPrevious, onNext, compact = false }: {
  label: string; info: ReactNode; previousDisabled: boolean; nextDisabled: boolean;
  onPrevious: () => void; onNext: () => void; compact?: boolean;
}) {
  return <nav className={compact ? "act-pager compact" : "act-pager"} aria-label={label}>
    <span className="act-pager-info">{info}</span>
    <div className="act-pager-buttons">
      <button type="button" className="ghost small" disabled={previousDisabled} onClick={onPrevious}>
        <ChevronLeft size={14} strokeWidth={1.9} aria-hidden="true" />Previous
      </button>
      <button type="button" className="ghost small" disabled={nextDisabled} onClick={onNext}>
        Next<ChevronRight size={14} strokeWidth={1.9} aria-hidden="true" />
      </button>
    </div>
  </nav>;
}
