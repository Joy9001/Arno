import { useState } from "react";
import type { FormEvent } from "react";
import { api, errorMessage } from "../api";
import type { Preference, PreferenceList } from "../chat/types";
import { formatMoment } from "../format";
import type { BackupStatus, CategoryTree, Settings as SettingsPayload } from "../types";
import { useApi } from "../useApi";
import { PasskeysCard } from "./Passkeys";

function BackupCard({ status }: { status: BackupStatus }) {
  return (
    <div className="settings-block">
      <h3>Backup status</h3>
      <p className="muted">
        The sidecar dumps the database and hands it to restic whenever the last success is over 24
        hours old.
      </p>
      <dl className="mini wide">
        <div>
          <dt>Last success</dt>
          <dd>{formatMoment(status.last_success_at)}</dd>
        </div>
        <div>
          <dt>Next due</dt>
          <dd>{formatMoment(status.due_at)}</dd>
        </div>
        <div>
          <dt>Alert</dt>
          <dd>{status.overdue ? "Overdue" : "Healthy"}</dd>
        </div>
        <div>
          <dt>Last attempt</dt>
          <dd>
            {status.last_attempt
              ? `${status.last_attempt.status} · ${formatMoment(status.last_attempt.finished_at)}`
              : "none yet"}
          </dd>
        </div>
      </dl>
      {status.last_attempt?.error && <p className="error">{status.last_attempt.error}</p>}
    </div>
  );
}

export function SettingsPanel() {
  const settings = useApi<SettingsPayload>("/api/settings");
  const tree = useApi<CategoryTree>("/api/categories");
  const backup = useApi<BackupStatus>("/api/backup/status");
  const preferences = useApi<PreferenceList>("/api/preferences");

  const [displayNameDraft, setDisplayNameDraft] = useState<string | null>(null);
  const [floorDraft, setFloorDraft] = useState<string | null>(null);
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);

  const displayName = displayNameDraft ?? settings.data?.profile.display_name ?? "";
  const floor =
    floorDraft ??
    (settings.data?.floor_paise === null || settings.data?.floor_paise === undefined
      ? ""
      : String(Math.round(settings.data.floor_paise / 100)));
  const categories = tree.data?.categories ?? [];
  const selectedCategoryId = categoryId || categories[0]?.id || "";

  async function saveProfile() {
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      const body: Record<string, unknown> = { display_name: displayName };
      if (floor.trim() !== "") {
        const rupees = Number(floor);
        if (!Number.isFinite(rupees) || rupees < 0) throw new Error("The floor must be a number.");
        body.floor_paise = Math.round(rupees * 100);
      }
      await api<SettingsPayload>("/api/settings", {
        method: "PATCH",
        body: JSON.stringify(body),
      });
      setMessage("Profile and floor saved. Every write has an undo group.");
      settings.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function toggleReducible(category: CategoryTree["categories"][number]) {
    try {
      await api(`/api/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({ reducible: !category.reducible }),
      });
      tree.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function addRule(event: FormEvent) {
    event.preventDefault();
    if (!merchant.trim() || !selectedCategoryId) return;
    try {
      await api("/api/category-rules", {
        method: "POST",
        body: JSON.stringify({
          merchant_pattern: merchant,
          category_id: selectedCategoryId,
          source: "user",
        }),
      });
      setMerchant("");
      setMessage("User rule saved. User rules always beat an LLM guess.");
      tree.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  async function removeRule(ruleId: string) {
    try {
      await api(`/api/category-rules/${ruleId}`, { method: "DELETE" });
      tree.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Settings</p>
          <h2>Profile, categories, backups, export</h2>
          <p className="muted">
            These are the L1 settings sections. Chat and settings write through the same ledger
            functions and the same audit log.
          </p>
        </div>
      </header>

      {error && <p className="error">{error}</p>}
      {message && <p className="notice">{message}</p>}

      <div className="settings-grid">
        <div className="settings-block">
          <h3>Profile and floor</h3>
          <p className="muted">The floor is the balance the projection must never cross.</p>
          <form
            onSubmit={(event) => {
              event.preventDefault();
              void saveProfile();
            }}
          >
            <label>
              Display name
              <input value={displayName} onChange={(event) => setDisplayNameDraft(event.target.value)} />
            </label>
            <label>
              Floor in rupees
              <input
                inputMode="decimal"
                value={floor}
                placeholder="25000"
                onChange={(event) => setFloorDraft(event.target.value)}
              />
            </label>
            <button type="submit" disabled={saving}>
              {saving ? "Saving…" : "Save profile and floor"}
            </button>
          </form>
          {settings.data && (
            <p className="fine">
              Base currency {settings.data.base_currency} · {settings.data.time_zone} · horizon{" "}
              {settings.data.default_horizon_days} days
            </p>
          )}
          {settings.data?.last_model_error_type && (
            <p className="error fine">
              Last model error: {settings.data.last_model_error_type}
              {settings.data.last_model_error_at
                ? ` · ${formatMoment(settings.data.last_model_error_at)}`
                : ""}
            </p>
          )}
        </div>

        {backup.data ? <BackupCard status={backup.data} /> : <div className="settings-block" />}

        <PasskeysCard />

        <div className="settings-block wide">
          <h3>Preferences (the agent's memory)</h3>
          <p className="muted">
            Remembered facts and rules. The agent saves these unprompted with a card and undo.
            Turning one off keeps it listed and reversible; deleting it for good blanks the text in
            the audit log too, and can't be undone.
          </p>
          {preferences.data?.preferences.length === 0 && (
            <p className="muted">Nothing remembered yet.</p>
          )}
          <div className="preference-list">
            {preferences.data?.preferences.map((item: Preference) => (
              <div key={item.id} className={item.active ? "preference" : "preference off"}>
                <div>
                  <p>{item.body || "(deleted)"}</p>
                  <p className="fine">
                    {item.created_by === "agent" ? "Saved by the agent" : "Saved by you"} ·{" "}
                    {formatMoment(item.created_at)} · {item.active ? "on" : "off"}
                  </p>
                </div>
                <div className="button-row">
                  <button
                    type="button"
                    className="ghost small"
                    onClick={async () => {
                      try {
                        await api(`/api/preferences/${item.id}`, {
                          method: "PATCH",
                          body: JSON.stringify({ active: !item.active }),
                        });
                        preferences.reload();
                      } catch (caught) {
                        setError(errorMessage(caught));
                      }
                    }}
                  >
                    {item.active ? "Turn off" : "Turn on"}
                  </button>
                  <button
                    type="button"
                    className="ghost small danger"
                    onClick={async () => {
                      if (
                        !window.confirm(
                          "Delete this preference for good? Its text is blanked in the audit log and this can't be undone."
                        )
                      ) {
                        return;
                      }
                      try {
                        await api(`/api/preferences/${item.id}`, { method: "DELETE" });
                        preferences.reload();
                      } catch (caught) {
                        setError(errorMessage(caught));
                      }
                    }}
                  >
                    Delete for good
                  </button>
                </div>
              </div>
            ))}
          </div>
        </div>

        <div className="settings-block wide">
          <h3>Categories</h3>
          <p className="muted">
            Reducible categories are the ones an affordability tradeoff may suggest cutting.
          </p>
          <div className="chip-list">
            {tree.data?.categories.map((category) => (
              <button
                key={category.id}
                type="button"
                className={`chip ${category.reducible ? "reducible" : ""}`}
                onClick={() => void toggleReducible(category)}
                title="Toggle whether this category is reducible"
              >
                {category.name}
                {category.reducible && <span> · reducible</span>}
              </button>
            ))}
          </div>

          <h3 className="section-title">Learned rules</h3>
          {tree.data?.rules.length === 0 && <p className="muted">No rules yet.</p>}
          {tree.data && tree.data.rules.length > 0 && (
            <div className="table-wrap">
              <table>
                <thead>
                  <tr>
                    <th>Merchant</th>
                    <th>Category</th>
                    <th>Source</th>
                    <th>Hits</th>
                    <th />
                  </tr>
                </thead>
                <tbody>
                  {tree.data.rules.map((rule) => (
                    <tr key={rule.id}>
                      <td>{rule.merchant_pattern}</td>
                      <td>{rule.category_name}</td>
                      <td>
                        <span className={`badge ${rule.source === "user" ? "good" : "muted"}`}>
                          {rule.source}
                        </span>
                      </td>
                      <td className="num">{rule.hits}</td>
                      <td>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => void removeRule(rule.id)}
                        >
                          Delete
                        </button>
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}

          <form className="inline-form" onSubmit={(event) => void addRule(event)}>
            <label>
              Merchant
              <input
                value={merchant}
                placeholder="Swiggy"
                onChange={(event) => setMerchant(event.target.value)}
              />
            </label>
            <label>
              Category
              <select value={selectedCategoryId} onChange={(event) => setCategoryId(event.target.value)}>
                {tree.data?.categories.map((category) => (
                  <option key={category.id} value={category.id}>
                    {category.name}
                  </option>
                ))}
              </select>
            </label>
            <button type="submit">Add user rule</button>
          </form>
        </div>

        <div className="settings-block wide">
          <h3>Export</h3>
          <p className="muted">
            A full JSON export, one CSV per table in a ZIP, and a plain `pg_dump`. The dump is the
            complete database, not a report.
          </p>
          <div className="button-row">
            <a className="button" href="/api/export/json" download>
              Download JSON
            </a>
            <a className="button" href="/api/export/csv" download>
              Download CSV ZIP
            </a>
            <a className="button" href="/api/export/pg_dump" download>
              Download pg_dump
            </a>
          </div>
          <p className="fine">
            The backup sidecar is separate: it dumps, encrypts with restic, and ships off-site. This
            page shows its last success above.
          </p>
        </div>
      </div>
    </section>
  );
}


