import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { X } from "lucide-react";
import { api, errorMessage } from "../api";
import { displayText } from "../chat/display-text";
import { formatDate, formatPaise } from "../format";
import { Loading } from "../Loading";
import type { RecapRows as RecapRowsPayload } from "../types";

/** The movements behind one recap figure, each a row the ledger can show again. */
export function RecapRows({
  periodId,
  reference,
  title,
  onClose,
}: {
  periodId: string;
  reference: { story: string; key: string | null };
  title: string;
  onClose: () => void;
}) {
  const { story, key } = reference;
  const [data, setData] = useState<RecapRowsPayload | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [version, setVersion] = useState(0);

  useEffect(() => {
    let alive = true;
    const params = new URLSearchParams({ story });
    if (key !== null) params.set("key", key);
    api<RecapRowsPayload>(`/api/recaps/${periodId}/rows?${params}`)
      .then((value) => {
        if (alive) {
          setData(value);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (alive) setError(errorMessage(caught));
      });
    return () => {
      alive = false;
    };
  }, [periodId, story, key, version]);

  const keyed = key !== null && ["where", "change", "frequent"].includes(story);
  const heading =
    keyed && key !== null ? `${displayText(title)} · ${displayText(key)}` : displayText(title);

  return (
    <Dialog.Root open onOpenChange={(open) => !open && onClose()}>
      <Dialog.Portal>
        <Dialog.Overlay className="dialog-overlay" />
        <Dialog.Content className="movement-dialog" aria-describedby={undefined}>
          <div className="queue-head">
            <Dialog.Title>{heading}</Dialog.Title>
            <Dialog.Close asChild>
              <button
                type="button"
                className="ghost small icon-only"
                aria-label="Close rows"
                title="Close rows"
              >
                <X size={15} strokeWidth={1.8} aria-hidden="true" />
              </button>
            </Dialog.Close>
          </div>
          {error ? (
            <>
              <p className="error" role="alert">
                The rows couldn&apos;t be read: {error}
              </p>
              <button
                type="button"
                className="pill-button"
                onClick={() => {
                  setError(null);
                  setVersion((n) => n + 1);
                }}
              >
                Retry
              </button>
            </>
          ) : !data ? (
            <Loading label="Opening the rows…" />
          ) : (
            <>
              <p className="muted fine">
                {data.rows.length} {data.rows.length === 1 ? "row" : "rows"} ·{" "}
                {displayText(data.sum.text)}
              </p>
              <ul className="recap-rows">
                {data.rows.map((row) => (
                  <li key={`${row.id}-${row.category ?? ""}`}>
                    <span>{formatDate(row.date)}</span>
                    <span>
                      {displayText(row.payee ?? row.description)}
                      {row.category && <em>{displayText(row.category)}</em>}
                    </span>
                    <b>{formatPaise(row.amount_paise)}</b>
                  </li>
                ))}
              </ul>
            </>
          )}
        </Dialog.Content>
      </Dialog.Portal>
    </Dialog.Root>
  );
}
