export function ExportScope({ excludedTables }: { excludedTables: string[] }) {
  return (
    <div className="stg-export-note">
      <p>JSON and CSV exclude these credential and secret tables: {excludedTables.join(", ")}.</p>
      <p>The full database dump includes credentials and secrets for recovery. Store it safely.</p>
      <p>Exports download to this device; no export is sent to a cloud destination.</p>
    </div>
  );
}
