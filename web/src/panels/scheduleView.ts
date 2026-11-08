import type { UpcomingEvent } from "../types";

export function paymentLabel(event: UpcomingEvent): string {
  switch (event.payment_status) {
    case "paid": return event.kind.startsWith("income") ? "Received" : "Paid";
    case "partial": return "Partial";
    case "recorded": return "Payment recorded";
    case "unmatched": return "Unmatched";
    case "untracked": return "Forecast";
    default: return "Scheduled";
  }
}

export function remainingAmount(event: UpcomingEvent): number {
  return (event.remaining_paise === undefined ? event.amount_paise : event.remaining_paise) ?? 0;
}
