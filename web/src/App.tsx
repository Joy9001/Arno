import { useCallback, useEffect, useState } from "react";
import { errorMessage } from "./api";
import { getAuthState, type AuthState } from "./auth";
import { Login } from "./Login";
import { Setup } from "./Setup";
import { Shell } from "./Shell";

export function App() {
  const [state, setState] = useState<AuthState | null>(null);
  const [error, setError] = useState<string | null>(null);

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

  if (error) {
    return (
      <main className="centered">
        <section className="card">
          <h1>Fin-Agent</h1>
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

  if (state.setup_required) {
    return <Setup onDone={refresh} />;
  }

  if (!state.authenticated) {
    return <Login onDone={refresh} />;
  }

  return <Shell user={state.user} onSignedOut={refresh} />;
}
