import { useState, type FormEvent } from "react";
import { errorMessage } from "./api";
import { login, type AuthState } from "./auth";
import { Busy } from "./Loading";
import { Orb } from "./Orb";

export function Login({ state, onDone }: { state: AuthState; onDone: () => void }) {
  const [passphrase, setPassphrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(event: FormEvent) {
    event.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await login(passphrase);
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
          <Orb size={120} live />
          Arno
        </div>
        <h1>Sign in</h1>
        {state.passphrase_enabled ? (
          <>
            <label>
              Passphrase
              <input
                type="password"
                autoComplete="current-password"
                value={passphrase}
                onChange={(event) => setPassphrase(event.target.value)}
                required
              />
            </label>
            {error && (
              <p className="error" role="alert">
                {error}
              </p>
            )}
            <button type="submit" disabled={busy || !passphrase}>
              {busy ? <Busy /> : "Sign in"}
            </button>
          </>
        ) : (
          <p className="muted">
            Open Arno through your Tailscale address while signed in to the owner account. Nothing
            here can be unlocked from this browser.
          </p>
        )}
      </form>
    </main>
  );
}
