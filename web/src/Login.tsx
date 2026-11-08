import { useState, type FormEvent } from "react";
import { errorMessage } from "./api";
import { loginWithPasskey } from "./auth";
import { Busy } from "./Loading";
import { Orb } from "./Orb";

export function Login({ onDone }: { onDone: () => void }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await loginWithPasskey();
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
        <div className="auth-brand">
          <Orb size={56} live />
          Fin-Agent
        </div>
        <h1>Sign in</h1>
        <p className="muted">This instance is behind one passkey. There is no password.</p>
        <p className="fine">
          Lost the passkey? Run <code>fin-agent passkeys</code> on the host.
        </p>
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy}>
          {busy ? (
            <>
              <Busy />
              Waiting for your passkey…
            </>
          ) : (
            "Sign in with a passkey"
          )}
        </button>
      </form>
    </main>
  );
}
