import { Fragment } from "react";
import { Button, Skeleton } from "@radix-ui/themes";
import { ExternalLink, RefreshCw } from "lucide-react";
import { useApi } from "../useApi";

type RuntimeRole = {
  role: string;
  configured: boolean;
  host: string | null;
  model: string | null;
};

type RuntimeInfoPayload = {
  roles: RuntimeRole[];
  phoenix_ui_url: string | null;
};

/**
 * Read-only runtime facts for the Advanced/Jobs section. The API exposes only a role's model
 * label and hostname, plus an operator-configured Phoenix browser URL; no credentials, paths,
 * queries, or internal collector address ever reach this component.
 */
export function RuntimeInfo() {
  const { data, error, loading, reload } = useApi<RuntimeInfoPayload>("/api/runtime-info");

  if (loading && !data) return <div role="status" aria-label="Loading model configuration"><Skeleton height="120px" /></div>;
  if (!data) {
    return (
      <p className="error" role="alert">
        {error ?? "Runtime details did not load."}{" "}
        <Button type="button" variant="soft" onClick={reload}>
          <RefreshCw size={14} aria-hidden="true" />
          Try again
        </Button>
      </p>
    );
  }

  return (
    <dl className="detail-grid" aria-label="Runtime configuration">
      {data.roles.map((role) => (
        <Fragment key={role.role}>
          <dt>{role.role}</dt>
          <dd>
            {role.configured
              ? `${role.model ?? "model label unavailable"} on ${role.host ?? "host unavailable"}`
              : "Not configured"}
          </dd>
        </Fragment>
      ))}
      <dt>Phoenix UI</dt>
      <dd>
        {data.phoenix_ui_url ? (
          <Button asChild variant="soft"><a href={data.phoenix_ui_url} target="_blank" rel="noreferrer">
            <ExternalLink size={14} aria-hidden="true" />Open Phoenix
          </a></Button>
        ) : (
          "Not configured"
        )}
      </dd>
    </dl>
  );
}
