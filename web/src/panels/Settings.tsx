import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { AlertDialog } from "radix-ui";
import {
  Activity,
  Bell,
  Brain,
  CalendarClock,
  CircleAlert,
  CircleCheck,
  Database,
  DatabaseBackup,
  Download,
  FileJson,
  FileSpreadsheet,
  LogOut,
  Pause,
  Pencil,
  Play,
  Plus,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Tags,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { api, errorMessage } from "../api";
import { askAgent } from "../ask";
import { NAVIGATE_EVENT } from "../nav";
import { RuntimeInfo } from "./RuntimeInfo";
import { ActivityPager } from "./ActivityPager";
import type { Preference, PreferenceList } from "../chat/types";
import { formatMoment } from "../format";
import { MemoryPrevious } from "./MemoryPrevious";
import { Busy } from "../Loading";
import { ExportScope } from "./ExportScope";
import { DeletionPreviewNote, DeletionScope } from "./DeletionScope";
import { HistoryPanel } from "./History";
import type {
  BackupStatus,
  CategoryTree,
  DeletionPreview,
  ScheduledQuestion,
  ScheduledQuestionList,
  Settings as SettingsPayload,
} from "../types";
import { useApi } from "../useApi";
import type { ApiState } from "../useApi";
import "./Settings.css";

const SECTION_GROUPS: Array<{
  label: string;
  items: Array<{ id: string; label: string; Icon: typeof UserRound }>;
}> = [
  {
    label: "Everyday",
    items: [
      { id: "profile", label: "Profile & floor", Icon: UserRound },
      { id: "categories", label: "Categories", Icon: Tags },
      { id: "notifications", label: "Notifications", Icon: Bell },
      { id: "questions", label: "Scheduled questions", Icon: CalendarClock },
      { id: "memory", label: "Agent memory", Icon: Brain },
      { id: "history", label: "History", Icon: RotateCcw },
    ],
  },
  {
    label: "Advanced",
    items: [
      { id: "diagnostics", label: "Diagnostics", Icon: Activity },
      { id: "backups", label: "Backups", Icon: DatabaseBackup },
      { id: "export", label: "Export", Icon: Download },
      { id: "deletion", label: "Delete everything", Icon: Trash2 },
    ],
  },
];

const SECTIONS = SECTION_GROUPS.flatMap((group) => group.items);
const RULE_PAGE_SIZE = 25;
const MEMORY_PAGE_SIZE = 10;
const QUESTION_PAGE_SIZE = 10;

function sectionFromHash(): string {
  const [rawPage, rawTab] = window.location.hash.slice(1).split("/");
  return rawPage === "settings" && SECTIONS.some((item) => item.id === rawTab)
    ? rawTab
    : "profile";
}

function useHashSection(): [string, (next: string) => void] {
  const [section, setSection] = useState(sectionFromHash);

  useEffect(() => {
    const sync = () => setSection(sectionFromHash());
    // goTo() pushes the hash without a popstate event, so the rail's job link needs this.
    const onNavigate = (event: Event) => {
      const detail = (event as CustomEvent<{ page?: string }>).detail;
      if (detail?.page === "settings") sync();
    };
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    window.addEventListener(NAVIGATE_EVENT, onNavigate);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
      window.removeEventListener(NAVIGATE_EVENT, onNavigate);
    };
  }, []);

  function choose(next: string) {
    setSection(next);
    if (window.location.hash.startsWith("#settings")) {
      window.history.pushState(null, "", `#settings/${next}`);
    }
  }

  return [section, choose];
}

function SettingsSection({
  id,
  title,
  description,
  children,
}: {
  id: string;
  title: string;
  description: string;
  children: ReactNode;
}) {
  const titleId = `stg-${id}-title`;
  return (
    <section className="stg-section" aria-labelledby={titleId}>
      <header className="stg-section-head">
        <h3 id={titleId}>{title}</h3>
        <p>{description}</p>
      </header>
      {children}
    </section>
  );
}

function SectionError({ message, onRetry }: { message: string; onRetry: () => void }) {
  return (
    <div className="stg-error" role="alert">
      <CircleAlert size={16} strokeWidth={1.8} aria-hidden="true" />
      <p>{message}</p>
      <button type="button" className="ghost small" onClick={onRetry}>
        <RefreshCw size={14} strokeWidth={1.8} aria-hidden="true" />
        Retry
      </button>
    </div>
  );
}

function Feedback({ error, message }: { error: string | null; message: string | null }) {
  if (!error && !message) return null;
  return (
    <div className="stg-feedback">
      {error && (
        <div className="stg-error" role="alert">
          <CircleAlert size={16} strokeWidth={1.8} aria-hidden="true" />
          <p>{error}</p>
        </div>
      )}
      {message && (
        <div className="stg-notice" role="status">
          <CircleCheck size={16} strokeWidth={1.8} aria-hidden="true" />
          <p>{message}</p>
        </div>
      )}
    </div>
  );
}

function SettingsSkeleton({
  label,
  rows = 3,
  fields = false,
}: {
  label: string;
  rows?: number;
  fields?: boolean;
}) {
  return (
    <div className="stg-skeleton" role="status">
      <div className="sk stg-sk stg-sk-title" />
      <div className="sk stg-sk stg-sk-line" />
      {fields && (
        <>
          <div className="sk stg-sk stg-sk-field" />
          <div className="sk stg-sk stg-sk-field" />
        </>
      )}
      {Array.from({ length: rows }, (_, index) => (
        <div className="stg-skeleton-row" key={index}>
          <div className="sk stg-sk stg-sk-line stg-sk-line-short" />
          <div className="sk stg-sk stg-sk-value" />
        </div>
      ))}
      {fields && <div className="sk stg-sk stg-sk-button" />}
      <span className="sr-only">{label}</span>
    </div>
  );
}

function Loadable<T>({
  state,
  label,
  skeleton,
  children,
}: {
  state: ApiState<T>;
  label: string;
  skeleton: ReactNode;
  children: (data: T) => ReactNode;
}) {
  if (!state.data) {
    return state.loading ? (
      <>{skeleton}</>
    ) : (
      <SectionError message={state.error ?? `Could not load ${label}.`} onRetry={state.reload} />
    );
  }
  return (
    <>
      {state.loading ? (
        <p className="stg-refreshing" role="status">
          <Busy />
          Refreshing {label}…
        </p>
      ) : (
        state.error && <SectionError message={state.error} onRetry={state.reload} />
      )}
      {children(state.data)}
    </>
  );
}

function Pager({
  page,
  pageCount,
  total,
  pageSize,
  onPage,
}: {
  page: number;
  pageCount: number;
  total: number;
  pageSize: number;
  onPage: (next: number) => void;
}) {
  if (total === 0) return null;
  return <ActivityPager label="Pagination" info={`${page * pageSize + 1}–${Math.min(total, (page + 1) * pageSize)} of ${total} · Page ${page + 1} of ${pageCount}`}
    previousDisabled={page === 0} nextDisabled={page >= pageCount - 1}
    onPrevious={() => onPage(page - 1)} onNext={() => onPage(page + 1)} />;
}

/** A Radix confirmation for destructive settings actions; the action fires only on confirm. */
function ConfirmButton({
  label,
  icon,
  title,
  description,
  confirmLabel,
  preview,
  onConfirm,
}: {
  label: string;
  icon: ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  /** The one row this action touches, shown with its retained data before the confirm. */
  preview?: { scope: "memory" | "statement"; id: string };
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <AlertDialog.Root open={open} onOpenChange={setOpen}>
      <button type="button" className="ghost small" onClick={() => setOpen(true)}>
        {icon}
        {label}
      </button>
      <AlertDialog.Portal>
        <AlertDialog.Overlay className="dialog-overlay" />
        <AlertDialog.Content className="sheet-dialog" style={{ maxWidth: 440 }}>
        <AlertDialog.Title>{title}</AlertDialog.Title>
        <AlertDialog.Description>{description}</AlertDialog.Description>
        {open && preview && <DeletionPreviewNote scope={preview.scope} id={preview.id} />}
        <div className="stg-confirm-actions">
          <AlertDialog.Cancel className="ghost small"><X size={14} aria-hidden="true" />Cancel</AlertDialog.Cancel>
          <AlertDialog.Action
            className="ghost small stg-danger"
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            {icon}{confirmLabel}
          </AlertDialog.Action>
        </div>
        </AlertDialog.Content>
      </AlertDialog.Portal>
    </AlertDialog.Root>
  );
}

/** The floor is stored in paise; reject ambiguous input instead of silently rounding it. */
function floorToPaise(raw: string): { paise: number } | { error: string } {
  const value = raw.replaceAll(",", "").trim();
  if (!value) return { paise: 0 };
  if (!/^\d+(?:\.\d{1,2})?$/.test(value)) {
    return { error: "Enter rupees with up to two decimals, for example 25,000 or 25000.50." };
  }
  const rupees = Number(value);
  const paise = Math.round(rupees * 100);
  if (!Number.isFinite(rupees) || !Number.isSafeInteger(paise)) {
    return { error: "That floor is too large." };
  }
  return { paise };
}

function ProfileSection({ settings }: { settings: ApiState<SettingsPayload> }) {
  return (
    <SettingsSection
      id="profile"
      title="Profile & floor"
      description="Set the details Arno uses when answering questions, and the balance the projection must not cross."
    >
      <Loadable
        state={settings}
        label="profile settings"
        skeleton={<SettingsSkeleton label="Loading profile settings…" fields />}
      >
        {(data) => <ProfileCard settings={data} onSaved={settings.reload} />}
      </Loadable>
    </SettingsSection>
  );
}

function ProfileCard({
  settings,
  onSaved,
}: {
  settings: SettingsPayload;
  onSaved: () => void;
}) {
  const [displayNameDraft, setDisplayNameDraft] = useState<string | null>(null);
  const [floorDraft, setFloorDraft] = useState<string | null>(null);
  const [nameError, setNameError] = useState<string | null>(null);
  const [floorError, setFloorError] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const displayName = displayNameDraft ?? settings.profile.display_name ?? "";
  const floor =
    floorDraft ?? (settings.floor_paise ? String(Math.round(settings.floor_paise / 100)) : "");

  async function save(event: FormEvent) {
    event.preventDefault();
    const trimmedName = displayName.trim();
    if (!trimmedName) {
      setNameError("Enter a display name.");
      return;
    }
    const parsed = floorToPaise(floor);
    if ("error" in parsed) {
      setFloorError(parsed.error);
      return;
    }
    setNameError(null);
    setFloorError(null);
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api<SettingsPayload>("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({ display_name: trimmedName, floor_paise: parsed.paise }),
      });
      setDisplayNameDraft(trimmedName);
      setFloorDraft(parsed.paise === 0 ? "" : String(parsed.paise / 100));
      setMessage("Profile and floor saved.");
      onSaved();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  return (
    <div className="stg-card">
      <form className="stg-form" onSubmit={(event) => void save(event)}>
        <label>
          Display name
          <input
            value={displayName}
            autoComplete="name"
            aria-invalid={Boolean(nameError)}
            aria-describedby={nameError ? "stg-name-error" : undefined}
            onChange={(event) => {
              setDisplayNameDraft(event.target.value);
              if (nameError && event.target.value.trim()) setNameError(null);
            }}
          />
          {nameError && (
            <span id="stg-name-error" className="error">
              {nameError}
            </span>
          )}
        </label>
        <label>
          Financial floor (INR)
          <input
            inputMode="decimal"
            value={floor}
            placeholder="25000"
            aria-invalid={Boolean(floorError)}
            aria-describedby={floorError ? "stg-floor-error stg-floor-hint" : "stg-floor-hint"}
            onChange={(event) => {
              setFloorDraft(event.target.value);
              const parsed = floorToPaise(event.target.value);
              if (floorError) setFloorError("error" in parsed ? parsed.error : null);
            }}
            onBlur={() => {
              const parsed = floorToPaise(floor);
              setFloorError("error" in parsed ? parsed.error : null);
            }}
          />
          <span id="stg-floor-hint" className="stg-hint">
            The projection must never cross this balance. Leave blank for no floor.
          </span>
          {floorError && (
            <span id="stg-floor-error" className="error">
              {floorError}
            </span>
          )}
        </label>
        <div className="stg-actions">
          <button type="submit" disabled={saving}>
            {saving ? (
              <>
                <Busy />
                Saving…
              </>
            ) : (
              <>
                <Save size={15} strokeWidth={1.8} aria-hidden="true" />
                Save changes
              </>
            )}
          </button>
        </div>
      </form>

      <Feedback error={error} message={message} />

      <dl className="stg-meta">
        <div>
          <dt>Currency</dt>
          <dd>{settings.base_currency}</dd>
        </div>
        <div>
          <dt>Time zone</dt>
          <dd>{settings.time_zone}</dd>
        </div>
        <div>
          <dt>Horizon</dt>
          <dd>{settings.default_horizon_days} days</dd>
        </div>
        {settings.updated_at && (
          <div>
            <dt>Updated</dt>
            <dd>{formatMoment(settings.updated_at)}</dd>
          </div>
        )}
      </dl>

      {settings.last_model_error_type && (
        <p className="stg-error-note">
          <CircleAlert size={14} strokeWidth={1.8} aria-hidden="true" />
          Last model error: {settings.last_model_error_type}
          {settings.last_model_error_at ? ` · ${formatMoment(settings.last_model_error_at)}` : ""}
        </p>
      )}
    </div>
  );
}

function CategoriesSection({ tree }: { tree: ApiState<CategoryTree> }) {
  return <SettingsSection id="categories" title="Categories" description="Review categories and merchant rules. Ask Arno to change them.">
    <Loadable state={tree} label="categories and rules" skeleton={<SettingsSkeleton label="Loading categories and rules…" rows={5} />}>
      {(data) => <CategoriesCard tree={data} onChanged={tree.reload} />}
    </Loadable>
  </SettingsSection>;
}

function CategoriesCard({ tree, onChanged }: { tree: CategoryTree; onChanged: () => void }) {
  const [page, setPage] = useState(0);
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [choices, setChoices] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const pageCount = Math.max(1, Math.ceil(tree.rules.length / RULE_PAGE_SIZE));
  const current = Math.min(page, pageCount - 1);
  const active = tree.categories.filter((category) => category.active);

  async function saveRule(body: object) {
    setBusy(true); setError(null); setMessage(null);
    try {
      await api("/api/category-rules", { method: "POST", body: JSON.stringify(body) });
      setMessage("Rule saved. Your rules take priority over agent guesses; past labels stay unchanged.");
      onChanged();
      return true;
    } catch (caught) { setError(errorMessage(caught)); return false; }
    finally { setBusy(false); }
  }

  async function removeRule(id: string) {
    setBusy(true); setError(null); setMessage(null);
    try {
      await api(`/api/category-rules/${encodeURIComponent(id)}`, { method: "DELETE" });
      setMessage("Rule deleted. Past labels stay unchanged; you can undo this in History.");
      onChanged();
    } catch (caught) { setError(errorMessage(caught)); }
    finally { setBusy(false); }
  }

  return <div className="stg-list">
    <button type="button" className="ghost small" onClick={() => askAgent("Help me add or change a category.")}>Ask Arno to manage categories</button>
    {tree.categories.map((category) => <div className="stg-list-row" key={category.id}><div className="stg-list-body"><strong>{category.name}</strong><p>{category.active ? "Active" : "Archived"} · {category.reducible ? "Reducible" : "Not reducible"}</p></div><button type="button" className="ghost small" onClick={() => askAgent(`Help me change category ${JSON.stringify(category.name)} (ID ${category.id}).`)}>Ask about {category.name}</button></div>)}
    <h4>Merchant rules</h4>
    <p className="stg-hint">Your rules take priority over agent guesses. Changes affect future captures, not past labels.</p>
    {tree.rules.length === 0 ? <p>No merchant rules yet.</p> : tree.rules.slice(current * RULE_PAGE_SIZE, (current + 1) * RULE_PAGE_SIZE).map((rule) => <div className="stg-list-row" key={rule.id}>
      <div className="stg-list-body"><strong>{rule.pattern}</strong><p>{rule.match} · {rule.source} · {rule.hits} matches</p></div>
      <div className="stg-list-actions"><select aria-label={`Category for rule ${rule.pattern}`} value={choices[rule.id] ?? rule.category_id} onChange={(event) => setChoices((old) => ({ ...old, [rule.id]: event.target.value }))}>
        {tree.categories.filter((category) => category.active || category.id === rule.category_id).map((category) => <option key={category.id} value={category.id}>{category.name}{category.active ? "" : " (archived)"}</option>)}
      </select>
      <button type="button" className="ghost small" disabled={busy} onClick={() => void saveRule({ pattern: rule.pattern, match: rule.match, priority: rule.priority, category_id: choices[rule.id] ?? rule.category_id, source: "user" })}>{rule.source === "llm" ? "Save as your rule" : "Save rule"}</button>
      <ConfirmButton label="Delete" icon={<Trash2 size={14} aria-hidden="true" />} title={`Delete the rule for “${rule.pattern}”?`} description="Future captures stop using this rule. Past labels stay unchanged." confirmLabel="Delete rule" onConfirm={() => void removeRule(rule.id)} />
      </div></div>)}
    <Pager page={current} pageCount={pageCount} total={tree.rules.length} pageSize={RULE_PAGE_SIZE} onPage={setPage} />
    <form className="stg-inline" aria-label="Add merchant rule" onSubmit={(event) => { event.preventDefault(); if (merchant.trim() && categoryId) void saveRule({ pattern: merchant.trim(), category_id: categoryId, source: "user" }).then((saved) => { if (saved) setMerchant(""); }); }}>
      <label>Merchant<input value={merchant} maxLength={200} required onChange={(event) => setMerchant(event.target.value)} /></label>
      <label>Category<select required value={categoryId} onChange={(event) => setCategoryId(event.target.value)}><option value="">Choose a category</option>{active.map((category) => <option key={category.id} value={category.id}>{category.name}</option>)}</select></label>
      <button type="submit" disabled={busy}>Add rule</button>
    </form>
    <Feedback error={error} message={message} />
  </div>;
}

function NotificationsSection({ settings }: { settings: ApiState<SettingsPayload> }) {
  return (
    <SettingsSection
      id="notifications"
      title="Notifications"
      description="Choose when reminders arrive."
    >
      <div className="stg-columns">
        <Loadable
          state={settings}
          label="notification settings"
          skeleton={<SettingsSkeleton label="Loading notification settings…" rows={2} />}
        >
          {(data) => <NotificationsCard settings={data} onSaved={settings.reload} />}
        </Loadable>
      </div>
    </SettingsSection>
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
    const lead = Number(leadDays);
    if (!Number.isInteger(lead) || lead < 0 || lead > 30) {
      setError("Reminder lead days must be a whole number from 0 to 30.");
      return;
    }
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api<SettingsPayload>("/api/settings", {
        method: "PATCH",
        body: JSON.stringify({
          notification_time: time,
          weekly_briefing_dow: Number(briefingDay),
          reminder_lead_days: lead,
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
    <div className="stg-card">
      <h3>Reminders and briefing</h3>
      <p className="stg-hint">
        Times are India time. The weekly briefing runs on the chosen day; reminders appear this
        many days before a bill, EMI, or card due date. Nightly checks keep their own schedule.
      </p>
      <form onSubmit={(event) => void save(event)}>
        <div className="stg-field-row">
          <label>
            Notification time
            <input
              type="time"
              value={time}
              required
              onChange={(event) => setTime(event.target.value)}
            />
          </label>
          <label>
            Weekly briefing day
            <select
              value={briefingDay}
              onChange={(event) => setBriefingDay(event.target.value)}
            >
              {["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"].map(
                (name, index) => (
                  <option key={name} value={index}>
                    {name}
                  </option>
                ),
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
              required
              onChange={(event) => setLeadDays(event.target.value)}
            />
          </label>
        </div>
        <div className="stg-actions">
          <button type="submit" disabled={saving}>
            {saving ? (
              <>
                <Busy />
                Saving…
              </>
            ) : (
              <>
                <Save size={15} strokeWidth={1.8} aria-hidden="true" />
                Save notification settings
              </>
            )}
          </button>
        </div>
      </form>
      <Feedback error={error} message={message} />
    </div>
  );
}

function ScheduledQuestionsSection() {
  const questions = useApi<ScheduledQuestionList>(
    "/api/scheduled-questions?include_inactive=true",
  );
  return (
    <SettingsSection
      id="questions"
      title="Scheduled questions"
      description="Read-only questions the agent runs on a schedule and answers into your inbox. Paused questions keep their history and can be resumed."
    >
      <Loadable
        state={questions}
        label="scheduled questions"
        skeleton={<SettingsSkeleton label="Loading scheduled questions…" rows={3} />}
      >
        {(data) => <ScheduledQuestionsCard data={data} onChanged={questions.reload} />}
      </Loadable>
    </SettingsSection>
  );
}

function ScheduledQuestionsCard({
  data,
  onChanged,
}: {
  data: ScheduledQuestionList;
  onChanged: () => void;
}) {
  const [prompt, setPrompt] = useState("");
  const [rule, setRule] = useState("FREQ=WEEKLY;BYDAY=SU");
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editRule, setEditRule] = useState("");
  const [busyId, setBusyId] = useState<string | null>(null);
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [page, setPage] = useState(0);

  const activeCount = data.questions.filter((question) => question.active).length;
  const pageCount = Math.max(1, Math.ceil(data.questions.length / QUESTION_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visibleQuestions = data.questions.slice(
    currentPage * QUESTION_PAGE_SIZE,
    (currentPage + 1) * QUESTION_PAGE_SIZE,
  );

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
          prompt: prompt.trim(),
          rule: rule.trim(),
        }),
      });
      setPrompt("");
      setMessage("Scheduled question saved. It runs read-only and answers into the inbox.");
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  function startEdit(question: ScheduledQuestion) {
    setEditingId(question.id);
    setEditPrompt(question.prompt);
    setEditRule(question.rule);
    setError(null);
    setMessage(null);
  }

  async function saveEdit(question: ScheduledQuestion) {
    if (!editPrompt.trim() || !editRule.trim()) return;
    setBusyId(question.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/scheduled-questions/${question.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          prompt: editPrompt.trim(),
          rule: editRule.trim(),
        }),
      });
      setEditingId(null);
      setMessage("Scheduled question updated.");
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  async function setActive(question: ScheduledQuestion, active: boolean) {
    setBusyId(question.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/scheduled-questions/${question.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      });
      setMessage(
        active
          ? "Question resumed. It runs again on its next schedule."
          : "Question paused. It will not run until you resume it.",
      );
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  return (
    <div className="stg-card">
      <p className="stg-hint">
        Each run is read-only, uses the pinned financial snapshot, and stops after 20 model
        requests. The instance also shares a $20 daily model ceiling.
      </p>

      <div className="stg-subhead">
        <h4>Your questions</h4>
        {data.questions.length > 0 && (
          <span className="stg-hint">
            {activeCount} active · {data.questions.length} total
          </span>
        )}
      </div>

      {data.questions.length === 0 ? (
        <div className="stg-empty">
          <p>No scheduled questions yet. Add one below to get a recurring answer in the inbox.</p>
        </div>
      ) : (
        <>
          <div className="stg-list">
            {visibleQuestions.map((question) => {
              const editing = editingId === question.id;
              return (
                <div className={`stg-list-row${question.active ? "" : " off"}`} key={question.id}>
                  <div className="stg-list-body">
                    {editing ? (
                      <div className="stg-question-edit">
                        <label>
                          Question
                          <textarea
                            rows={2}
                            value={editPrompt}
                            required
                            maxLength={2000}
                            onChange={(event) => setEditPrompt(event.target.value)}
                          />
                        </label>
                        <div className="stg-field-row">
                          <label>
                            Repeat rule (RRULE)
                            <input
                              value={editRule}
                              required
                              maxLength={500}
                              aria-describedby={`stg-edit-rule-hint-${question.id}`}
                              onChange={(event) => setEditRule(event.target.value)}
                            />
                          </label>
                        </div>
                        <p id={`stg-edit-rule-hint-${question.id}`} className="stg-hint">
                          Times follow your notification time.
                        </p>
                      </div>
                    ) : (
                      <>
                        <p className="stg-question">{question.prompt}</p>
                        <p className="stg-list-meta">
                          {question.active
                            ? `Active · next ${formatMoment(question.next_run_at)}`
                            : "Paused · no next run"}{" "}
                          · last{" "}
                          {question.last_run_at
                            ? formatMoment(question.last_run_at)
                            : "never run"}{" "}
                        </p>
                      </>
                    )}
                  </div>
                  <div className="stg-list-actions">
                    {editing ? (
                      <>
                        <button
                          type="button"
                          className="ghost small"
                          disabled={
                            busyId === question.id || !editPrompt.trim() || !editRule.trim()
                          }
                          onClick={() => void saveEdit(question)}
                        >
                          <Save size={14} strokeWidth={1.8} aria-hidden="true" />
                          Save
                        </button>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => setEditingId(null)}
                        >
                          <X size={14} strokeWidth={1.8} aria-hidden="true" />
                          Cancel
                        </button>
                      </>
                    ) : (
                      <>
                        <button
                          type="button"
                          className="ghost small"
                          onClick={() => startEdit(question)}
                        >
                          <Pencil size={14} strokeWidth={1.8} aria-hidden="true" />
                          Edit
                        </button>
                        <button
                          type="button"
                          className="ghost small"
                          disabled={busyId === question.id}
                          onClick={() => void setActive(question, !question.active)}
                        >
                          {question.active ? (
                            <>
                              <Pause size={14} strokeWidth={1.8} aria-hidden="true" />
                              Pause
                            </>
                          ) : (
                            <>
                              <Play size={14} strokeWidth={1.8} aria-hidden="true" />
                              Resume
                            </>
                          )}
                        </button>
                      </>
                    )}
                  </div>
                </div>
              );
            })}
          </div>
          <Pager
            page={currentPage}
            pageCount={pageCount}
            total={data.questions.length}
            pageSize={QUESTION_PAGE_SIZE}
            onPage={setPage}
          />
        </>
      )}

      <form className="stg-question-form" onSubmit={(event) => void add(event)}>
        <label>
          Question
          <textarea
            rows={2}
            value={prompt}
            required
            maxLength={2000}
            placeholder="Every Sunday, what did I spend on food?"
            onChange={(event) => setPrompt(event.target.value)}
          />
        </label>
        <div className="stg-field-row">
          <label>
            Repeat rule (RRULE)
            <input
              value={rule}
              required
              maxLength={500}
              aria-describedby="stg-rule-hint"
              onChange={(event) => setRule(event.target.value)}
            />
          </label>
        </div>
        <p id="stg-rule-hint" className="stg-hint">
          Use an RRULE such as <code>FREQ=WEEKLY;BYDAY=SU</code>. Times follow your notification
          time.
        </p>
        <div className="stg-actions">
          <button type="submit" disabled={saving || !prompt.trim() || !rule.trim()}>
            {saving ? (
              <>
                <Busy />
                Saving…
              </>
            ) : (
              <>
                <Plus size={15} strokeWidth={1.8} aria-hidden="true" />
                Add scheduled question
              </>
            )}
          </button>
        </div>
      </form>

      <Feedback error={error} message={message} />
    </div>
  );
}

function DiagnosticsSection() {
  return (
    <SettingsSection
      id="diagnostics"
      title="Diagnostics"
      description="The models Arno is configured to use and how they are doing."
    >
      <div className="stg-card">
        <h4>Models and diagnostics</h4>
        <RuntimeInfo />
      </div>
    </SettingsSection>
  );
}

function BackupsSection({ backup }: { backup: ApiState<BackupStatus> }) {
  return (
    <SettingsSection
      id="backups"
      title="Backups"
      description="The restic sidecar dumps the database off-site. These are the latest recorded status and recovery instructions."
    >
      <Loadable
        state={backup}
        label="backup status"
        skeleton={<SettingsSkeleton label="Loading backup status…" rows={4} />}
      >
        {(data) => <BackupCard status={data} />}
      </Loadable>
    </SettingsSection>
  );
}

function BackupCard({ status }: { status: BackupStatus }) {
  const health = status.overdue ? "Overdue" : status.due ? "Due" : "Healthy";
  const healthBadge = status.overdue ? "danger" : status.due ? "warn" : "good";
  const lastAttempt = status.last_attempt;

  return (
    <div className="stg-card">
      <div className="stg-subhead">
        <h4>Backup status</h4>
        <span className={`badge ${healthBadge}`}>
          {status.overdue ? (
            <CircleAlert size={13} strokeWidth={1.8} aria-hidden="true" />
          ) : (
            <CircleCheck size={13} strokeWidth={1.8} aria-hidden="true" />
          )}
          {health}
        </span>
      </div>
      <p className="stg-hint">
        The sidecar dumps the database and hands it to restic whenever the last success is over 24
        hours old.
      </p>

      <dl className="stg-status-grid">
        <div>
          <dt>Last success</dt>
          <dd>
            {status.last_success_at
              ? formatMoment(status.last_success_at)
              : "No successful backup yet"}
          </dd>
        </div>
        <div>
          <dt>Next due</dt>
          <dd>{status.due_at ? formatMoment(status.due_at) : "Not scheduled"}</dd>
        </div>
        <div>
          <dt>Last attempt</dt>
          <dd>
            {lastAttempt
              ? `${lastAttempt.status} · ${formatMoment(lastAttempt.finished_at)}`
              : "No attempt recorded yet"}
          </dd>
        </div>
        <div>
          <dt>Checked</dt>
          <dd>{formatMoment(status.checked_at)}</dd>
        </div>
      </dl>

      {lastAttempt?.error && (
        <p className="stg-error-note">
          <CircleAlert size={14} strokeWidth={1.8} aria-hidden="true" />
          {lastAttempt.error}
        </p>
      )}

      <div className="stg-callout">
        <ShieldCheck size={16} strokeWidth={1.8} aria-hidden="true" />
        <p>
          Restore runs on the host, not from this page. <code>arno restore</code> restores the
          newest restic snapshot and verifies every table; see the README for the full drill.
        </p>
      </div>
    </div>
  );
}

const EXPORT_FORMATS = [
  {
    id: "json",
    title: "JSON",
    description: "Data tables and records in one readable file, excluding secret tables.",
    path: "/api/export/json",
    filename: "arno-export.json",
    action: "Download JSON",
    Icon: FileJson,
  },
  {
    id: "csv",
    title: "CSV ZIP",
    description: "One CSV per data table, excluding secret tables, packed in a ZIP.",
    path: "/api/export/csv",
    filename: "arno-export.zip",
    action: "Download CSV ZIP",
    Icon: FileSpreadsheet,
  },
  {
    id: "dump",
    title: "Database dump",
    description: "The complete PostgreSQL database as SQL. Best for a full recovery or migration.",
    path: "/api/export/pg_dump",
    filename: "arno-export.sql",
    action: "Download database dump",
    Icon: Database,
  },
] as const;

function ExportSection() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const scope = useApi<{ excluded_tables: string[] }>("/api/export/scope");

  async function download(format: (typeof EXPORT_FORMATS)[number]) {
    setBusy(format.id);
    setError(null);
    try {
      const response = await fetch(format.path, { credentials: "same-origin" });
      if (!response.ok) {
        const problem = (await response.json().catch(() => null)) as { detail?: string } | null;
        throw new Error(problem?.detail ?? `Download failed (${response.status})`);
      }
      const url = URL.createObjectURL(await response.blob());
      const link = document.createElement("a");
      link.href = url;
      link.download = format.filename;
      link.click();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  return (
    <SettingsSection
      id="export"
      title="Export"
      description="Download your data in the form that fits the job."
    >
      <div className="stg-card">
        <div className="stg-export-list">
          {EXPORT_FORMATS.map((format) => (
            <div className="stg-export-row" key={format.id}>
              <div className="stg-export-copy">
                <span className="stg-export-icon">
                  <format.Icon size={17} strokeWidth={1.7} aria-hidden="true" />
                </span>
                <div>
                  <h4>{format.title}</h4>
                  <p>{format.description}</p>
                  <code>{format.filename}</code>
                </div>
              </div>
              <button
                type="button"
                className="ghost small"
                disabled={busy !== null}
                onClick={() => void download(format)}
              >
                {busy === format.id ? (
                  <>
                    <Busy />
                    Preparing…
                  </>
                ) : (
                  <>
                    <Download size={14} strokeWidth={1.8} aria-hidden="true" />
                    {format.action}
                  </>
                )}
              </button>
            </div>
          ))}
        </div>

        {scope.data && <ExportScope excludedTables={scope.data.excluded_tables} />}
        {scope.loading && <p>Loading export scope…</p>}
        {scope.error && <p role="alert">Could not load export scope: {scope.error}</p>}

        <Feedback error={error} message={null} />
      </div>
    </SettingsSection>
  );
}

function DeletionSection() {
  const preview = useApi<DeletionPreview>("/api/deletion/preview?scope=everything");
  const [phrase, setPhrase] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const ready = phrase.trim().toLowerCase() === "delete everything";

  async function erase() {
    setBusy(true);
    setError(null);
    setMessage(null);
    try {
      const result = await api<{ total: number }>("/api/deletion/everything", {
        method: "POST",
        body: JSON.stringify({ confirm: phrase.trim() }),
      });
      setPhrase("");
      setMessage(
        `Deleted ${result.total} records. Your sign-in and instance settings still work.`,
      );
      preview.reload();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(false);
    }
  }

  return (
    <SettingsSection
      id="deletion"
      title="Delete everything"
      description="Remove every financial and user row on this machine. The preview shows what goes, what stays, and where the data still lives. This cannot be undone."
    >
      <div className="stg-card">
        <Loadable
          state={preview}
          label="the deletion scope"
          skeleton={<SettingsSkeleton label="Counting what a delete would remove…" rows={5} />}
        >
          {(data) => <DeletionScope preview={data} />}
        </Loadable>

        <div className="stg-deletion-confirm">
          <label>
            Type <code>delete everything</code> to confirm
            <input
              value={phrase}
              autoComplete="off"
              disabled={busy}
              onChange={(event) => setPhrase(event.target.value)}
            />
          </label>
          <button
            type="button"
            className="ghost stg-danger"
            disabled={!ready || busy}
            onClick={() => void erase()}
          >
            {busy ? (
              <>
                <Busy />
                Deleting…
              </>
            ) : (
              <>
                <Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />
                Delete everything
              </>
            )}
          </button>
        </div>

        <Feedback error={error} message={message} />
      </div>
    </SettingsSection>
  );
}

function MemorySection({ preferences }: { preferences: ApiState<PreferenceList> }) {
  return <SettingsSection id="memory" title="Agent memory" description="Review saved memories or ask Arno to change them.">
    <Loadable state={preferences} label="agent memory" skeleton={<SettingsSkeleton label="Loading agent memory…" rows={4} />}>
      {(data) => <MemoryCard data={data} onChanged={preferences.reload} />}
    </Loadable>
  </SettingsSection>;
}

function MemoryCard({ data, onChanged }: { data: PreferenceList; onChanged: () => void }) {
  const [page, setPage] = useState(0);
  const [error, setError] = useState<string | null>(null);
  const count = Math.max(1, Math.ceil(data.preferences.length / MEMORY_PAGE_SIZE));
  const current = Math.min(page, count - 1);
  async function remove(item: Preference) {
    setError(null);
    try { await api(`/api/preferences/${encodeURIComponent(item.id)}`, { method: "DELETE" }); onChanged(); }
    catch (caught) { setError(errorMessage(caught)); }
  }
  return <div className="memory-workspace"><p className="stg-hint">Deleting a memory for good blanks its text in the audit log and cannot be undone.</p>
    {data.preferences.length === 0 && <p>Nothing remembered yet.</p>}
    <div className="stg-list">{data.preferences.slice(current * MEMORY_PAGE_SIZE, (current + 1) * MEMORY_PAGE_SIZE).map((item) => <div className="stg-list-row" key={item.id}>
      <div className="stg-list-body"><p>{item.body || "(deleted)"}</p><p className="stg-list-meta">{item.kind} · {item.active ? "on" : "off"} · {item.created_by === "agent" ? "Saved by the agent" : "Saved by you"} · {formatMoment(item.created_at)}</p><MemoryPrevious previous={item.previous} /></div>
      <div className="stg-list-actions"><button type="button" className="ghost small" onClick={() => askAgent(`Help me change memory ${item.id}: ${JSON.stringify(item.body)}`)}>Ask to change</button>
        <ConfirmButton label="Delete for good" icon={<Trash2 size={14} aria-hidden="true" />} title="Delete this memory for good?" description="Its text is blanked in the audit log and this cannot be undone." confirmLabel="Delete for good" preview={{ scope: "memory", id: item.id }} onConfirm={() => void remove(item)} />
      </div></div>)}</div>
    <Pager page={current} pageCount={count} total={data.preferences.length} pageSize={MEMORY_PAGE_SIZE} onPage={setPage} /><Feedback error={error} message={null} />
  </div>;
}

export function SettingsPanel({ onSignOut }: { onSignOut: () => void }) {
  const [section, chooseSection] = useHashSection();
  const settings = useApi<SettingsPayload>("/api/settings");
  const tree = useApi<CategoryTree>("/api/categories");
  const backup = useApi<BackupStatus>("/api/backup/status");
  const preferences = useApi<PreferenceList>("/api/preferences");
  const reduceMotion = useReducedMotion() ?? false;

  function renderSection() {
    switch (section) {
      case "categories":
        return <CategoriesSection tree={tree} />;
      case "notifications":
        return <NotificationsSection settings={settings} />;
      case "questions":
        return <ScheduledQuestionsSection />;
      case "diagnostics":
        return <DiagnosticsSection />;
      case "backups":
        return <BackupsSection backup={backup} />;
      case "export":
        return <ExportSection />;
      case "deletion":
        return <DeletionSection />;
      case "memory":
        return <MemorySection preferences={preferences} />;
      case "history":
        return <HistoryPanel />;
      case "profile":
      default:
        return <ProfileSection settings={settings} />;
    }
  }

  return (
    <section className="panel stg-panel">
      <header className="panel-head stg-head">
        <div>
          <h2>Settings</h2>
          <p className="muted">Manage your preferences and your private instance.</p>
        </div>
      </header>

      <div className="stg-layout">
        <nav className="stg-nav" aria-label="Settings sections">
          {SECTION_GROUPS.map((group) => (
            <div className="stg-nav-group" key={group.label}>
              <p className="stg-nav-label">{group.label}</p>
              {group.items.map(({ id, label, Icon }) => (
                <a
                  key={id}
                  href={`#settings/${id}`}
                  className="stg-nav-link"
                  aria-current={section === id ? "location" : undefined}
                >
                  <Icon size={15} strokeWidth={1.7} aria-hidden="true" />
                  <span>{label}</span>
                </a>
              ))}
            </div>
          ))}
        </nav>

        <div className="stg-content">
          <div className="stg-mobile-nav">
            <label htmlFor="stg-section-select">Settings section</label>
            <select
              id="stg-section-select"
              value={section}
              onChange={(event) => chooseSection(event.target.value)}
            >
              {SECTION_GROUPS.map((group) => (
                <optgroup key={group.label} label={group.label}>
                  {group.items.map((item) => (
                    <option key={item.id} value={item.id}>
                      {item.label}
                    </option>
                  ))}
                </optgroup>
              ))}
            </select>
          </div>

          <motion.div
            key={section}
            initial={reduceMotion ? false : { opacity: 0, y: 6 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: reduceMotion ? 0 : 0.18, ease: [0.22, 1, 0.36, 1] }}
          >
            {renderSection()}
          </motion.div>
        </div>
      </div>

      {/* Phones have no sidebar foot, so signing out closes the settings page instead. */}
      <footer className="stg-signout-row">
        <button type="button" className="ghost" onClick={onSignOut}>
          <LogOut size={14} strokeWidth={1.8} aria-hidden="true" />
          Sign out
        </button>
      </footer>
    </section>
  );
}
