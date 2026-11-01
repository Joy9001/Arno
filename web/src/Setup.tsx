import { useState, type FormEvent } from "react";
import { errorMessage } from "./api";
import { registerFirstPasskey } from "./auth";

export function Setup({ onDone }: { onDone: () => void }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await registerFirstPasskey(token.trim());
      onDone();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <main className="centered">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <p className="eyebrow">First run</p>
        <h1>Set up Fin-Agent</h1>
        <p className="muted">
          Enter the setup token printed in the API log (<code>docker compose logs api</code> or
          <code> fin-agent setup-token</code>), then create the passkey this instance will use.
        </p>
        <label htmlFor="setup-token">Setup token</label>
        <input
          id="setup-token"
          name="token"
          autoComplete="off"
          autoFocus
          required
          minLength={8}
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || token.trim().length < 8}>
          {busy ? "Waiting for your passkey…" : "Create passkey"}
        </button>
        <p className="fine">
          Passkeys need HTTPS and a stable hostname. Over Tailscale, open this page at your
          <code> *.ts.net</code> name.
        </p>
      </form>
    </main>
  );
}
