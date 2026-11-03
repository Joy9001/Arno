import { useEffect, useState } from "react";
import type { FormEvent } from "react";
import { api, errorMessage } from "../api";
import { ensureFreshPasskey } from "../auth";
import type { Preference, PreferenceList } from "../chat/types";
import { formatMoment } from "../format";
import { disablePush, enablePush, pushPermission, pushSupported } from "../push";
import type {
  BackupStatus,
  CategoryTree,
  JobStatus,
  ScheduledQuestion,
  ScheduledQuestionList,
  Settings as SettingsPayload,
} from "../types";
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

function NotificationsCard({
  settings,
  onSaved,
}: {
  settings: SettingsPayload;
  onSaved: () => void;
}) {
  const [time, setTime] = useState(settings.notification_time.slice(0, 5));
  const [briefingDay, setBriefingDay] = useState(String(settings.weekly_briefing_dow));
  const [leadDays, setLeadDays] = useState(String(settings.reminder_lead_days));
  const [saving, setSaving] = useState(false);
  const [message, setMessage] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function save(event: FormEvent) {
    event.preventDefault();
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api<SettingsPayload>("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({
          notification_time: time,
          weekly_briefing_dow: Number(briefingDay),
          reminder_lead_days: Number(leadDays),
        }),
      });
      setMessage("Notification times and reminder lead days saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="settings-block">
      <h3>Notifications and reminders</h3>
      <p className="muted">
        Times are India time. The weekly briefing runs on the chosen day; reminders appear this
        many days before a bill, EMI, or card due date. Nightly risk, pattern, and backup checks
        run on their own schedule.
      </p>
      <form onSubmit={(event) => void save(event)}>
        <label>
          Notification time
          <input type="time" value={time} onChange={(event) => setTime(event.target.value)} />
        </label>
        <label>
          Weekly briefing day
          <select value={briefingDay} onChange={(event) => setBriefingDay(event.target.value)}>
            {["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map(
              (name, index) => (
                <option key={name} value={index}>
                  {name}
                </option>
              )
            )}
          </select>
        </label>
        <label>
          Reminder lead days
          <input
            type="number"
            min={0}
            max={30}
            value={leadDays}
            onChange={(event) => setLeadDays(event.target.value)}
          />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Save notification settings"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {message && <p className="notice">{message}</p>}
    </div>
  );
}

function PushCard() {
  const [supported] = useState(() => pushSupported());
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    "default"
  );
  const [subscribed, setSubscribed] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    if (!supported) return;
    void pushPermission().then(setPermission);
    navigator.serviceWorker
      .getRegistration()
      .then((registration) => registration?.pushManager.getSubscription())
      .then((subscription) => setSubscribed(Boolean(subscription)))
      .catch(() => setSubscribed(false));
  }, [supported]);

  async function toggle() {
    setBusy(true);
    setError(null);
    try {
      if (subscribed) {
        await disablePush();
        setSubscribed(false);
      } else {
        const result = await enablePush();
        setPermission(result);
        setSubscribed(result === "granted");
      }
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <div className="settings-block">
      <h3>Web push</h3>
      <p className="muted">
        Push only says that something is ready, such as “Your weekly briefing is ready”. No amount,
        merchant, or balance is sent through the browser vendor's push service; the full item stays
        in the Inbox.
      </p>
      {!supported && <p className="muted">This browser does not support web push.</p>}
      {supported && (
        <>
          <p className="fine">
            Permission: {permission} · {subscribed ? "subscribed" : "not subscribed"}
          </p>
          <button type="button" onClick={() => void toggle()} disabled={busy}>
            {busy ? "Working…" : subscribed ? "Disable push" : "Enable push"}
          </button>
        </>
      )}
      {error && <p className="error">{error}</p>}
    </div>
  );
}

function ScheduledQuestionsCard() {
  const questions = useApi<ScheduledQuestionList>("/api/scheduled-questions");
  const [prompt, setPrompt] = useState("");
  const [rule, setRule] = useState("FREQ=WEEKLY;BYDAY=SU");
  const [spend, setSpend] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  async function add(event: FormEvent) {
    event.preventDefault();
    if (!prompt.trim() || !rule.trim()) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api("/api/scheduled-questions", {
        method: "POST",
        body: JSON.stringify({
          prompt,
          rule,
          spend_limit_usd: spend.trim() === "" ? null : Number(spend),
        }),
      });
      setPrompt("");
      setMessage("Scheduled question saved. It runs read-only and answers into the Inbox.");
      questions.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function archive(question: ScheduledQuestion) {
    try {
      await api(`/api/scheduled-questions/${question.id}`, { method: "DELETE" });
      questions.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="settings-block wide">
      <h3>Scheduled questions</h3>
      <p className="muted">
        The agent runs these read-only, with no write tools, the pinned financial snapshot, and a
        ceiling of 20 model requests. The default run ceiling is $1 against the shared $20 daily
        ceiling. Answers land in the Inbox.
      </p>
      {questions.data?.questions.length === 0 && (
        <p className="muted">No scheduled questions yet.</p>
      )}
      <div className="preference-list">
        {questions.data?.questions.map((question) => (
          <div key={question.id} className="preference">
            <div>
              <p>{question.prompt}</p>
              <p className="fine">
                {question.rule} · next {formatMoment(question.next_run_at)} · last run{" "}
                {formatMoment(question.last_run_at)} · limit $
                {question.spend_limit_usd ?? question.default_spend_limit_usd}
              </p>
            </div>
            <button type="button" className="ghost small" onClick={() => void archive(question)}>
              Archive
            </button>
          </div>
        ))}
      </div>
      <form className="scheduled-form" onSubmit={(event) => void add(event)}>
        <label>
          Question
          <textarea
            rows={2}
            value={prompt}
            placeholder="Every Sunday, what did I spend on food?"
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
        <label>
          RRULE
          <input value={rule} onChange={(event) => setRule(event.target.value)} />
        </label>
        <label>
          Spend limit (USD)
          <input
            inputMode="decimal"
            value={spend}
            placeholder="1.00"
            onChange={(event) => setSpend(event.target.value)}
          />
        </label>
        <button type="submit" disabled={saving}>
          {saving ? "Saving…" : "Add scheduled question"}
        </button>
      </form>
      {error && <p className="error">{error}</p>}
      {message && <p className="notice">{message}</p>}
    </div>
  );
}

function JobsCard() {
  const jobs = useApi<JobStatus>("/api/jobs");
  return (
    <div className="settings-block wide">
      <h3>Job status</h3>
      <p className="muted">
        The one loop in the API process runs the recurring schedules. A missed run fires once when
        the machine wakes, and a period key prevents a duplicate.
      </p>
      {jobs.data && (
        <p className="fine">
          {Object.entries(jobs.data.counts)
            .map(([status, count]) => `${count} ${status}`)
            .join(" · ") || "no jobs yet"}
        </p>
      )}
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Schedule</th>
              <th>Rule</th>
              <th>Next</th>
              <th>Last</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data?.schedules.map((schedule) => (
              <tr key={schedule.id} className={schedule.active ? "" : "muted-row"}>
                <td>{schedule.label}</td>
                <td className="fine">{schedule.rule}</td>
                <td>{formatMoment(schedule.next_run_at)}</td>
                <td>{formatMoment(schedule.last_run_at)}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <h3 className="section-title">Recent jobs</h3>
      <div className="table-wrap">
        <table>
          <thead>
            <tr>
              <th>Job</th>
              <th>Status</th>
              <th>Attempts</th>
              <th>Finished</th>
              <th>Error</th>
            </tr>
          </thead>
          <tbody>
            {jobs.data?.recent.map((job) => (
              <tr key={job.id}>
                <td>{job.kind.replaceAll("_", " ")}</td>
                <td>
                  <span className={`badge ${job.status === "failed" ? "danger" : "muted"}`}>
                    {job.status}
                  </span>
                </td>
                <td className="num">{job.attempts}</td>
                <td>{formatMoment(job.finished_at)}</td>
                <td className="fine">{job.last_error ?? ""}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {jobs.error && <p className="error">{jobs.error}</p>}
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
  const [downloading, setDownloading] = useState(false);

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

  async function downloadExport(path: string, filename: string) {
    setDownloading(true);
    setError(null);
    try {
      await ensureFreshPasskey();
      const response = await fetch(path, { credentials: "same-origin" });
      if (!response.ok) {
        const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(problem?.detail ?? `Download failed (${response.status})`);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setDownloading(false);
    }
  }

  return (
    <section className="panel">
      <header className="panel-head">
        <div>
          <p className="eyebrow">Settings</p>
          <h2>Profile, categories, notifications, jobs, backups, export</h2>
          <p className="muted">
            These are the L1 and L6 settings sections. Chat and settings write through the same
            ledger functions and the same audit log.
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

        {settings.data && (
          <NotificationsCard settings={settings.data} onSaved={settings.reload} />
        )}

        <PushCard />

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

        <ScheduledQuestionsCard />

        <JobsCard />

        <div className="settings-block wide">
          <h3>Export</h3>
          <p className="muted">
            A full JSON export, one CSV per table in a ZIP, and a plain `pg_dump`. The dump is the
            complete database, not a report. Each download asks for your passkey first.
          </p>
          <div className="button-row">
            <button
              type="button"
              className="button"
              disabled={downloading}
              onClick={() => void downloadExport("/api/export/json", "fin-agent-export.json")}
            >
              Download JSON
            </button>
            <button
              type="button"
              className="button"
              disabled={downloading}
              onClick={() => void downloadExport("/api/export/csv", "fin-agent-export.zip")}
            >
              Download CSV ZIP
            </button>
            <button
              type="button"
              className="button"
              disabled={downloading}
              onClick={() => void downloadExport("/api/export/pg_dump", "fin-agent-export.sql")}
            >
              Download pg_dump
            </button>
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


