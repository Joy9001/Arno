import { RotateCcw } from "lucide-react";
import { ActionReceipt } from "./widgets/action";
import { displayText } from "./display-text";
import { formatRupees } from "./format";

export function CaptureCard({
  data,
  onRetry,
}: {
  data: Record<string, unknown>;
  onRetry: (kind: string, attachmentId: string) => void;
}) {
  const kind = String(data.kind ?? "");
  const id = String(data.attachment_id ?? "");
  const retry = data.card === "capture-retry";
  return (
    <div className="chat-card capture-card">
      {(!retry || kind === "photo") && (
        <img
          src={`/api/attachments/${encodeURIComponent(id)}`}
          alt={
            retry
              ? "The photo that could not be read"
              : "The shared receipt or payment screenshot"
          }
        />
      )}
      <div className="capture-fields">
        <strong>
          {displayText(
            String(data.title ?? (retry ? "Capture failed" : "Photo logged")),
          )}
        </strong>
        {retry ? (
          <>
            <p>
              {displayText(String(data.note ?? "The file is saved. Retry it."))}
            </p>
            {data.retryable !== false && (
              <button type="button" onClick={() => onRetry(kind, id)}>
                <RotateCcw size={14} aria-hidden="true" /> Retry
              </button>
            )}
          </>
        ) : (
          <>
            {typeof data.amount_paise === "number" && (
              <span>{formatRupees(data.amount_paise)}</span>
            )}
            <dl>
              <dt>Merchant</dt>
              <dd>{displayText(String(data.merchant ?? "unknown"))}</dd>
              <dt>Date</dt>
              <dd>{displayText(String(data.date ?? "unknown"))}</dd>
              <dt>Method</dt>
              <dd>{displayText(String(data.method ?? "unknown"))}</dd>
              <dt>UPI ref</dt>
              <dd>{displayText(String(data.upi_ref ?? "not shown"))}</dd>
            </dl>
            <ActionReceipt data={data} />
          </>
        )}
      </div>
    </div>
  );
}

export function NumberFlags({ amounts }: { amounts: string[] }) {
  if (!amounts.length) return null;
  return (
    <p className="note">
      Not verified against a ledger row: {amounts.map(displayText).join(", ")}.
      Check them in the ledger before you rely on them.
    </p>
  );
}
