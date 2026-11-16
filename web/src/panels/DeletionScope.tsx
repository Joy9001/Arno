import type { DeletionPreview } from "../types";
import { useApi } from "../useApi";

const ROLE_LABEL: Record<string, string> = {
  chat: "Chat",
  vision: "Statement pages",
  stt: "Voice",
};

/** What a deletion removes, what it keeps, and where the data still lives. */
export function DeletionScope({ preview }: { preview: DeletionPreview }) {
  const total = preview.removes.reduce((sum, row) => sum + row.count, 0);
  const label = (row: DeletionPreview["removes"][number]) => {
    const noun = row.table === "parser_task_records" ? "file" : "row";
    return row.count === 1 ? noun : `${noun}s`;
  };
  return (
    <div className="stg-deletion">
      <div className="stg-deletion-block">
        <h4>What this deletes</h4>
        {preview.removes.length === 0 ? (
          <p className="fine">No user data is stored right now.</p>
        ) : (
          <ul className="stg-deletion-list">
            {preview.removes.map((row) => (
              <li key={row.table}>
                <code>{row.table}</code>
                <span>
                  {row.count} {label(row)}
                </span>
              </li>
            ))}
          </ul>
        )}
        <p className="fine">
          {total} {total === 1 ? "record" : "records"} in total.
        </p>
      </div>

      <div className="stg-deletion-block">
        <h4>Rows this keeps</h4>
        <ul className="stg-deletion-why">
          {preview.retains.map((item) => (
            <li key={item.what}>
              <strong>{item.what}</strong>
              <span>{item.why}</span>
            </li>
          ))}
        </ul>
      </div>

      <div className="stg-deletion-block">
        <h4>Backups</h4>
        <p>{preview.backups.policy}</p>
        <p className="stg-hint">{preview.backups.note}</p>
      </div>

      <div className="stg-deletion-block">
        <h4>Model providers</h4>
        {preview.providers.length === 0 ? (
          <p className="fine">
            No model provider is configured, so nothing has been sent anywhere.
          </p>
        ) : (
          <ul className="stg-deletion-why">
            {preview.providers.map((item) => (
              <li key={item.role}>
                <strong>
                  {ROLE_LABEL[item.role] ?? item.role} · {item.provider} · {item.host}
                </strong>
                <span>{item.note}</span>
              </li>
            ))}
          </ul>
        )}
      </div>
    </div>
  );
}

/** The same scope for one memory or one statement file, fetched when its confirm opens. */
export function DeletionPreviewNote({ scope, id }: { scope: "memory" | "statement"; id: string }) {
  const preview = useApi<DeletionPreview>(
    `/api/deletion/preview?scope=${scope}&id=${encodeURIComponent(id)}`,
  );
  if (!preview.data) {
    return (
      <p className="fine" role="status">
        {preview.error ?? "Checking what this delete touches…"}
      </p>
    );
  }
  return <DeletionScope preview={preview.data} />;
}
