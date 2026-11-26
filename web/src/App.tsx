import { useCallback, useEffect, useState } from "react";
import { api, errorMessage } from "./api";
import { getAuthState, type AuthState } from "./auth";
import { Login } from "./Login";
import { Shell } from "./Shell";
import type { Settings, User } from "./types";

export function App() {
  const [state, setState] = useState<AuthState | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [user, setUser] = useState<User | null>(null);

  const refresh = useCallback(async () => {
    try {
      setState(await getAuthState());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }, []);

  useEffect(() => {
    let active = true;
    getAuthState()
      .then((next) => {
        if (active) {
          setState(next);
          setError(null);
        }
      })
      .catch((caught: unknown) => {
        if (active) setError(errorMessage(caught));
      });
    return () => {
      active = false;
    };
  }, []);

  const authenticated = state?.authenticated ?? false;
  useEffect(() => {
    if (!authenticated) return;
    api<Settings>("/api/settings")
      .then((settings) => setUser({ display_name: settings.profile.display_name ?? "You" }))
      .catch(() => undefined);
  }, [authenticated]);

  if (error) {
    return (
      <main className="centered">
        <section className="card">
          <h1>Arno</h1>
          <p className="error" role="alert">
            {error}
          </p>
          <button type="button" onClick={() => void refresh()}>
            Try again
          </button>
        </section>
      </main>
    );
  }

  if (state === null) {
    return (
      <main className="centered">
        <p className="muted" aria-live="polite">
          Loading…
        </p>
      </main>
    );
  }

  if (!state.authenticated) {
    return <Login state={state} onDone={refresh} />;
  }

  return <Shell user={user} onSignedOut={refresh} />;
}
