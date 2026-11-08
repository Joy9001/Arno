import { startRegistration } from "@simplewebauthn/browser";
import { useState, type FormEvent } from "react";
import { errorMessage } from "./api";
import { Busy } from "./Loading";
import { Orb } from "./Orb";
import {
  beginEnrollment,
  verifyEnrollment,
  type AuthState,
  type EnrollmentKind,
} from "./auth";

/**
 * Read `#t=` once at module load, before React StrictMode can remount. Enrollment links always
 * arrive as a full page load, so this runs before the first render.
 */
const initialToken = takeTokenFromHash();

function takeTokenFromHash(): string {
  const hash = window.location.hash;
  if (!hash) return "";
  const token = new URLSearchParams(hash.slice(1)).get("t")?.trim() ?? "";
  if (token) {
    window.history.replaceState(null, "", window.location.pathname + window.location.search);
  }
  return token;
}

export function Setup({ authState }: { authState: AuthState }) {
  const [token, setToken] = useState(initialToken);
  const [kind, setKind] = useState<EnrollmentKind | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  const origin = window.location.origin;
  const originMismatch = origin !== authState.rp_origin;
  const rightLink = `${authState.rp_origin}/setup${
    initialToken ? `#t=${encodeURIComponent(initialToken)}` : ""
  }`;

  async function submit(event: FormEvent) {
    event.preventDefault();
    const clean = token.trim();
    if (clean.length < 8) return;

    setBusy(true);
    setError(null);
    try {
      const begin = await beginEnrollment(clean);
      setKind(begin.kind);
      const credential = await startRegistration({ optionsJSON: begin.options });
      await verifyEnrollment(begin.challenge_id, credential);
      window.location.assign("/");
    } catch (caught) {
      setError(errorMessage(caught));
      setBusy(false);
    }
  }

  if (originMismatch) {
    return (
      <main className="centered">
        <section className="card">
          <div className="auth-brand">
            <Orb size={120} live />
            Fin-Agent
          </div>
          <h1>Open this page at the passkey origin</h1>
          <p className="muted">
            Passkeys for this instance are bound to <code>{authState.rp_origin}</code>. This page
            is at <code>{origin}</code>, so the browser would refuse to create a usable passkey.
          </p>
          <a className="button" href={rightLink}>
            Continue at {authState.rp_origin}
          </a>
        </section>
      </main>
    );
  }

  const addDevice = kind === "add_device";
  const setupComplete = !authState.setup_required && !authState.authenticated;

  return (
    <main className="centered">
      <form className="card" onSubmit={(event) => void submit(event)}>
        <div className="auth-brand">
          <Orb size={120} live />
          Fin-Agent
        </div>
        <h1>{addDevice ? "Add this device" : "Set up Fin-Agent"}</h1>

        {setupComplete && (
          <p className="notice">
            Setup is already complete. Paste an add-device token from Settings → Passkeys, or
            <a href="/"> sign in</a>.
          </p>
        )}
        {authState.authenticated && (
          <p className="notice">
            You are signed in. This link adds another passkey to your account.
          </p>
        )}

        <p className="muted">
          {addDevice
            ? "This link adds one passkey to your account. It works once and expires ten minutes after it was created."
            : "Enter the token from the enrollment link printed by the host, then create the passkey this instance will use."}
        </p>
        {initialToken && (
          <p className="fine">Enrollment link detected. The token below is already filled in.</p>
        )}

        <label htmlFor="enrollment-token">Enrollment token</label>
        <input
          id="enrollment-token"
          name="token"
          autoComplete="off"
          autoFocus
          required
          minLength={8}
          value={token}
          aria-describedby="enrollment-token-hint"
          onChange={(event) => setToken(event.target.value)}
        />
        <p id="enrollment-token-hint" className="fine">
          Paste the whole token from the enrollment link; it is at least 8 characters.
        </p>
        {token.trim().length > 0 && token.trim().length < 8 && (
          <p className="error fine" role="alert">
            That token looks too short. Paste the whole token from the link.
          </p>
        )}
        {error && (
          <p className="error" role="alert">
            {error}
          </p>
        )}
        <button type="submit" disabled={busy || token.trim().length < 8}>
          {busy ? (
            <>
              <Busy />
              Waiting for your passkey…
            </>
          ) : addDevice ? (
            "Add this device"
          ) : (
            "Create passkey"
          )}
        </button>
        <p className="fine">
          Passkeys need HTTPS and a stable hostname. This page must be opened at{" "}
          <code>{authState.rp_origin}</code>; a passkey made on another origin won&apos;t work.
          No link? Run <code>fin-agent setup</code> on the host, or use Settings → Passkeys → Add
          a device.
        </p>
      </form>
    </main>
  );
}
