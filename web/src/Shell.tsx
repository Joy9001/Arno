import { useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { logout, type Health, type User } from "./auth";

export function Shell({ user, onSignedOut }: { user: User | null; onSignedOut: () => void }) {
  const [health, setHealth] = useState<Health | null>(null);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    api<Health>("/api/health")
      .then((result) => {
        if (active) setHealth(result);
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
  }, []);

  async function signOut() {
    try {
      await logout();
      onSignedOut();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="app">
      <header className="topbar">
        <div>
          <p className="eyebrow">Fin-Agent</p>
          <h1>{user?.display_name ?? "Fin-Agent"}</h1>
        </div>
        <button type="button" className="ghost" onClick={() => void signOut()}>
          Sign out
        </button>
      </header>

      <main className="content">
        <section className="card">
          <h2>Foundations</h2>
          <p className="muted">
            The shell, the database, and passkey login are in place. Accounts, movements, chat,
            and the projection land in the layers after this one.
          </p>
          <dl className="status">
            <div>
              <dt>API</dt>
              <dd>{health ? health.status : error ? "unreachable" : "checking…"}</dd>
            </div>
            <div>
              <dt>Database</dt>
              <dd>{health ? health.database : "—"}</dd>
            </div>
            <div>
              <dt>Schema</dt>
              <dd>{health ? `v${health.schema_version}` : "—"}</dd>
            </div>
          </dl>
          {error && (
            <p className="error" role="alert">
              {error}
            </p>
          )}
        </section>

        <section className="card">
          <h2>Reached over your tailnet</h2>
          <p className="muted">
            Nothing is exposed to the open internet. The API listens on loopback, and
            <code> tailscale serve</code> gives it HTTPS on your private network.
          </p>
        </section>
      </main>
    </div>
  );
}
