import { QRCodeSVG } from "qrcode.react";
import { useEffect, useState } from "react";
import { AlertDialog, Button, Skeleton } from "@radix-ui/themes";
import { Copy, LogOut, Pencil, Plus, RefreshCw, Trash2, X } from "lucide-react";
import { api, errorMessage } from "../api";
import {
  createEnrollment,
  deletePasskey,
  ensureFreshPasskey,
  listPasskeys,
  logoutAll,
  renamePasskey,
} from "../auth";
import { formatMoment } from "../format";
import type { Enrollment, Passkey } from "../types";

type CurrentSession = {
  user: { id: string; display_name: string };
  session: {
    id: string;
    expires_at: string;
    reverified_at: string | null;
    fresh: boolean;
  };
};

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
  const [current, setCurrent] = useState<CurrentSession | null>(null);
  const [sessionError, setSessionError] = useState<string | null>(null);
  const [pendingDelete, setPendingDelete] = useState<Passkey | null>(null);
  const [confirmSignOutAll, setConfirmSignOutAll] = useState(false);
  const [signingOut, setSigningOut] = useState(false);

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
    api<CurrentSession>("/api/auth/session")
      .then((session) => {
        if (active) setCurrent(session);
      })
      .catch((caught: unknown) => {
        if (active) setSessionError(errorMessage(caught));
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
    try {
      await ensureFreshPasskey();
      await deletePasskey(passkey.id);
      await refresh();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function signOutAll() {
    setSigningOut(true);
    setError(null);
    try {
      await logoutAll();
      window.location.reload();
    } catch (caught) {
      setError(errorMessage(caught));
      setSigningOut(false);
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

      <div className="stg-session">
        <div className="stg-subhead">
          <h4>Current session</h4>
          {current?.session.fresh && <span className="stg-hint">passkey check fresh</span>}
        </div>
        {current ? (
          <p className="stg-list-meta">
            Signed in as {current.user.display_name}. This session expires{" "}
            {formatMoment(current.session.expires_at)}.{" "}
            {current.session.fresh
              ? "Sensitive actions won't ask for your passkey again for a few minutes."
              : "Sensitive actions ask for your passkey."}
          </p>
        ) : sessionError ? (
          <p className="error" role="alert">
            {sessionError}
          </p>
        ) : (
          <Skeleton>
            <div style={{ height: 18, width: 260 }} />
          </Skeleton>
        )}
        <div className="stg-actions">
          <button
            type="button"
            className="ghost small"
            disabled={signingOut}
            onClick={() => setConfirmSignOutAll(true)}
          >
            <LogOut size={14} aria-hidden="true" />
            {signingOut ? "Signing out…" : "Sign out all devices"}
          </button>
        </div>
      </div>

      <div className="button-row">
        <button type="button" className="ghost" disabled={busy} onClick={() => void addDevice()}>
          <Plus size={16} aria-hidden="true" />
          {busy ? "Creating link…" : "Add a device"}
        </button>
      </div>

      {error && (
        <div role="alert">
          <p className="error">{error}</p>
          {passkeys?.length === 0 && (
            <button type="button" className="ghost small" onClick={() => void refresh()}>
              <RefreshCw size={14} aria-hidden="true" /> Retry loading passkeys
            </button>
          )}
        </div>
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
              title="Add a device to Arno"
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
                <Copy size={14} aria-hidden="true" />
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
        {passkeys === null && (
          <div role="status" aria-label="Loading passkeys" aria-busy="true">
            <Skeleton><div style={{ height: 48, marginBottom: 8 }} /></Skeleton>
            <Skeleton><div style={{ height: 48 }} /></Skeleton>
          </div>
        )}
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
                  <Pencil size={14} aria-hidden="true" />
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
                  onClick={() => setPendingDelete(passkey)}
                >
                  <Trash2 size={14} aria-hidden="true" />
                  Delete
                </button>
              </div>
            </div>
          );
        })}
      </div>

      <AlertDialog.Root
        open={pendingDelete !== null}
        onOpenChange={(open) => {
          if (!open) setPendingDelete(null);
        }}
      >
        <AlertDialog.Content maxWidth="420px">
          <AlertDialog.Title>Delete “{pendingDelete?.name}”?</AlertDialog.Title>
          <AlertDialog.Description size="2">
            That device will need another passkey to sign in. The last passkey can&apos;t be
            deleted.
          </AlertDialog.Description>
          <div className="stg-confirm-actions">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray"><X size={14} aria-hidden="true" />Cancel</Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action
              className="ghost small stg-danger"
              onClick={() => {
                const passkey = pendingDelete;
                setPendingDelete(null);
                if (passkey) void remove(passkey);
              }}
            >
              <Button color="red"><Trash2 size={14} aria-hidden="true" />Delete passkey</Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Root>

      <AlertDialog.Root open={confirmSignOutAll} onOpenChange={setConfirmSignOutAll}>
        <AlertDialog.Content maxWidth="420px">
          <AlertDialog.Title>Sign out every device?</AlertDialog.Title>
          <AlertDialog.Description size="2">
            Every session ends, including this one. You will need a passkey to sign in again.
          </AlertDialog.Description>
          <div className="stg-confirm-actions">
            <AlertDialog.Cancel>
              <Button variant="soft" color="gray"><X size={14} aria-hidden="true" />Cancel</Button>
            </AlertDialog.Cancel>
            <AlertDialog.Action className="ghost small stg-danger" onClick={() => void signOutAll()}>
              <Button color="red"><LogOut size={14} aria-hidden="true" />Sign out all</Button>
            </AlertDialog.Action>
          </div>
        </AlertDialog.Content>
      </AlertDialog.Root>
    </div>
  );
}
