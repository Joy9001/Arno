import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { formatDate, formatPaise } from "../format";
import type { UpcomingEvent } from "../types";
import { askAgent } from "../ask";
import { paymentLabel } from "./scheduleView";

export function SchedulePaymentDetails({ event }: { event: UpcomingEvent }) {
  const payments = event.payments ?? [];
  const matchPrompt = `Find an existing ledger movement to match to schedule ${event.name} (ID ${event.schedule_id}), occurrence ${event.date}. Show me the candidate before matching; don't record another payment.`;
  return (
    <Dialog.Root>
      <Dialog.Trigger asChild>
        <button type="button" className="ghost small" aria-label={`Payment details: ${event.name} on ${event.date}`}>Details</button>
      </Dialog.Trigger>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="movement-dialog" aria-describedby="schedule-payment-description">
          <div className="queue-head">
            <Dialog.Title>{event.name}</Dialog.Title>
            <Dialog.Close asChild>
              <button type="button" className="ghost small icon-only" aria-label="Close payment details"><X size={15} aria-hidden="true" /></button>
            </Dialog.Close>
          </div>
          <Dialog.Description id="schedule-payment-description">
            {formatDate(event.date)} · {paymentLabel(event)}
          </Dialog.Description>
          <dl className="detail-grid">
            <dt>Expected</dt><dd>{formatPaise(event.amount_paise)}</dd>
            <dt>Matched</dt><dd>{formatPaise(event.paid_paise ?? 0)}</dd>
            <dt>Remaining</dt><dd>{formatPaise(event.remaining_paise)}</dd>
          </dl>
          {event.amount_paise === null && <p className="moneyv-basis">The expected amount is unknown, so a recorded payment cannot establish that this occurrence is fully covered.</p>}
          <h3>Recorded payments</h3>
          {payments.length === 0 ? (
            <p className="moneyv-basis">No ledger movement is matched. This does not establish whether the occurrence has been paid.</p>
          ) : (
            <ul className="schedule-payments">
              {payments.map((payment) => (
                <li key={payment.id}>
                  <div className="schedule-payment-copy">
                    <strong>{payment.description || "Recorded movement"}</strong>
                    <span className="moneyv-meta">{formatDate(payment.date)} · {payment.active ? "Matched" : "Reversed · excluded"}</span>
                    <code className="moneyv-meta">{payment.movement_id}</code>
                  </div>
                  <strong>{formatPaise(payment.amount_paise)}</strong>
                  <Dialog.Close asChild>
                    <button type="button" className="ask-link" onClick={() => askAgent(`Unlink movement ${payment.movement_id} from schedule ${event.name} (ID ${event.schedule_id}), occurrence ${event.date}. Keep the ledger movement.`)}>Ask to unlink</button>
                  </Dialog.Close>
                </li>
              ))}
            </ul>
          )}
          <p className="moneyv-basis">Matching assigns existing ledger money; it does not record another transaction. Reversed movements do not count. Schedule confirmation does not mean paid.</p>
          <Dialog.Close asChild><button type="button" className="ask-link" onClick={() => askAgent(matchPrompt)}>Find a payment</button></Dialog.Close>
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
