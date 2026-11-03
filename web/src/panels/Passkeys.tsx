import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";
import { errorMessage } from "../api";
import { createEnrollment, deletePasskey, ensureFreshPasskey, listPasskeys, renamePasskey } from "../auth";
import { formatMoment } from "../format";
import type { Enrollment, Passkey } from "../types";

function remainingLabel(expiresAt: string, now: number): { ms: number; label: string } {
  const ms = Math.max(0, new Date(expiresAt).getTime() - now);
  const minutes = Math.floor(ms / 60000);
  const seconds = Math.floor((ms % 60000) / 1000);
  return { ms, label: `${minutes}:${String(seconds).padStart(2, "0")}` };
}

export function PasskeysCard() {
  const [passkeys, setPasskeys] = useState<Passkey[] | null>(null);
  const [drafts, setDrafts] = useState<Record<string, string>>({});
  const [enrollment, setEnrollment] = useState<Enrollment | null>(null);
  const [now, setNow] = useState(() => Date.now());
  const [busy, setBusy] = useState(false);
  const [copied, setCopied] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    let active = true;
    listPasskeys()
      .then((rows) => {
        if (active) setPasskeys(rows);
      })
      .catch((caught: unknown) => {
        if (active) {
          setError(errorMessage(caught));
          setPasskeys([]);
        }
      });
    return () => {
      active = false;
    };
  }, []);

  useEffect(() => {
    if (!enrollment) return;
    const timer = window.setInterval(() => setNow(Date.now()), 1000);
    return () => window.clearInterval(timer);
  }, [enrollment]);

  async function refresh() {
    try {
      setPasskeys(await listPasskeys());
      setError(null);
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function addDevice() {
    setBusy(true);
    setError(null);
    try {
      await ensureFreshPasskey();
      setEnrollment(await createEnrollment());
      setNow(Date.now());
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  async function copyLink() {
    if (!enrollment) return;
    try {
      await navigator.clipboard.writeText(enrollment.link);
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    } catch {
      setError("Could not copy the link. Select it and copy manually.");
    }
  }

  async function saveName(passkey: Passkey) {
    const name = (drafts[passkey.id] ?? passkey.name).trim();
    if (!name || name === passkey.name) return;
    try {
      await ensureFreshPasskey();
      await renamePasskey(passkey.id, name);
      setDrafts((current) => {
        const next = { ...current };
        delete next[passkey.id];
        return next;
      });
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function remove(passkey: Passkey) {
    if (!window.confirm(`Delete "${passkey.name}"? This device will need another passkey to sign in.`)) {
      return;
    }
    try {
      await ensureFreshPasskey();
      await deletePasskey(passkey.id);
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  const countdown = enrollment
    ? remainingLabel(enrollment.expires_at, now)
    : { ms: 0, label: "0:00" };

  return (
    <div className="settings-block wide">
      <h3>Passkeys</h3>
      <p className="muted">
        A passkey is the only way to sign in. Add one for each device you use; the last one
        can&apos;t be deleted.
      </p>

      <div className="button-row">
        <button type="button" className="ghost" disabled={busy} onClick={() => void addDevice()}>
          {busy ? "Creating link…" : "Add a device"}
        </button>
      </div>

      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}

      {enrollment && countdown.ms > 0 && (
        <div className="enrollment-card">
          <div className="qr-frame">
            <QRCodeSVG
              value={enrollment.link}
              size={168}
              level="M"
              marginSize={2}
              bgColor="#ffffff"
              fgColor="#0b0f14"
              title="Add a device to Fin-Agent"
            />
          </div>
          <div className="enrollment-details">
            <p>
              Scan this code on the new device, or open the link. It adds one passkey and works
              once.
            </p>
            <code className="enrollment-link">{enrollment.link}</code>
            <div className="button-row">
              <button type="button" className="ghost small" onClick={() => void copyLink()}>
                {copied ? "Copied" : "Copy link"}
              </button>
            </div>
            <p className="fine">Expires in {countdown.label}</p>
          </div>
        </div>
      )}
      {enrollment && countdown.ms <= 0 && (
        <p className="notice">
          That link expired. Create a new one; the old link will not work.
        </p>
      )}

      <div className="passkey-list">
        {passkeys === null && <p className="muted">Loading…</p>}
        {passkeys?.map((passkey) => {
          const draft = drafts[passkey.id] ?? passkey.name;
          return (
            <div className="passkey-row" key={passkey.id}>
              <div className="passkey-meta">
                <input
                  aria-label={`Name for ${passkey.name}`}
                  value={draft}
                  maxLength={100}
                  onChange={(event) =>
                    setDrafts((current) => ({ ...current, [passkey.id]: event.target.value }))
                  }
                />
                <p className="fine">
                  Created {formatMoment(passkey.created_at)} · last used{" "}
                  {formatMoment(passkey.last_used_at)}
                </p>
              </div>
              <div className="passkey-actions">
                <button
                  type="button"
                  className="ghost small"
                  disabled={draft.trim() === passkey.name || draft.trim() === ""}
                  onClick={() => void saveName(passkey)}
                >
                  Rename
                </button>
                <button
                  type="button"
                  className="ghost small"
                  disabled={(passkeys?.length ?? 0) <= 1}
                  title={
                    (passkeys?.length ?? 0) <= 1
                      ? "The last passkey can't be deleted"
                      : "Delete this passkey"
                  }
                  onClick={() => void remove(passkey)}
                >
                  Delete
                </button>
              </div>
            </div>
          );
        })}
      </div>
    </div>
  );
}
