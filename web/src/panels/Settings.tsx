import { useEffect, useState } from "react";
import type { FormEvent, ReactNode } from "react";
import { AlertDialog, Button, Skeleton } from "@radix-ui/themes";
import {
  Activity,
  Bell,
  BellOff,
  BellRing,
  Brain,
  CalendarClock,
  ChevronLeft,
  ChevronRight,
  CircleAlert,
  CircleCheck,
  Database,
  DatabaseBackup,
  Download,
  FileJson,
  FileSpreadsheet,
  KeyRound,
  LogOut,
  Pause,
  Pencil,
  Play,
  Plus,
  Power,
  PowerOff,
  RefreshCw,
  RotateCcw,
  Save,
  ShieldCheck,
  Tag,
  Tags,
  Trash2,
  UserRound,
  X,
} from "lucide-react";
import { motion, useReducedMotion } from "motion/react";
import { api, errorMessage } from "../api";
import { ensureFreshPasskey } from "../auth";
import { RuntimeInfo } from "./RuntimeInfo";
import type { Preference, PreferenceList } from "../chat/types";
import { formatMoment } from "../format";
import { Busy } from "../Loading";
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
import type { ApiState } from "../useApi";
import { PasskeysCard } from "./Passkeys";
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
      { id: "passkeys", label: "Passkeys", Icon: KeyRound },
      { id: "questions", label: "Scheduled questions", Icon: CalendarClock },
      { id: "memory", label: "Agent memory", Icon: Brain },
    ],
  },
  {
    label: "Advanced",
    items: [
      { id: "jobs", label: "Jobs", Icon: Activity },
      { id: "backups", label: "Backups", Icon: DatabaseBackup },
      { id: "export", label: "Export", Icon: Download },
    ],
  },
];

const SECTIONS = SECTION_GROUPS.flatMap((group) => group.items);
const RULE_PAGE_SIZE = 25;
const MEMORY_PAGE_SIZE = 10;
const MEMORY_MAX_LENGTH = 2000;
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
    window.addEventListener("hashchange", sync);
    window.addEventListener("popstate", sync);
    return () => {
      window.removeEventListener("hashchange", sync);
      window.removeEventListener("popstate", sync);
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
      <Skeleton className="sk stg-sk stg-sk-title" />
      <Skeleton className="sk stg-sk stg-sk-line" />
      {fields && (
        <>
          <Skeleton className="sk stg-sk stg-sk-field" />
          <Skeleton className="sk stg-sk stg-sk-field" />
        </>
      )}
      {Array.from({ length: rows }, (_, index) => (
        <div className="stg-skeleton-row" key={index}>
          <Skeleton className="sk stg-sk stg-sk-line stg-sk-line-short" />
          <Skeleton className="sk stg-sk stg-sk-value" />
        </div>
      ))}
      {fields && <Skeleton className="sk stg-sk stg-sk-button" />}
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
  const first = page * pageSize + 1;
  const last = Math.min(total, (page + 1) * pageSize);
  return (
    <div className="stg-pager">
      <span className="stg-pager-count">
        {first}–{last} of {total}
      </span>
      <div className="stg-pager-buttons" role="group" aria-label="Pagination">
        <button
          type="button"
          className="ghost small"
          disabled={page === 0}
          onClick={() => onPage(page - 1)}
        >
          <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />
          Previous
        </button>
        <span className="stg-page-number">
          Page {page + 1} of {pageCount}
        </span>
        <button
          type="button"
          className="ghost small"
          disabled={page >= pageCount - 1}
          onClick={() => onPage(page + 1)}
        >
          Next
          <ChevronRight size={14} strokeWidth={1.8} aria-hidden="true" />
        </button>
      </div>
    </div>
  );
}

function TableRegion({ label, children }: { label: string; children: ReactNode }) {
  return (
    <div className="stg-table-region" role="region" aria-label={label} tabIndex={0}>
      {children}
    </div>
  );
}

/** A Radix confirmation for destructive settings actions; the action fires only on confirm. */
function ConfirmButton({
  label,
  icon,
  title,
  description,
  confirmLabel,
  onConfirm,
}: {
  label: string;
  icon: ReactNode;
  title: string;
  description: string;
  confirmLabel: string;
  onConfirm: () => void;
}) {
  const [open, setOpen] = useState(false);
  return (
    <AlertDialog.Root open={open} onOpenChange={setOpen}>
      <button type="button" className="ghost small" onClick={() => setOpen(true)}>
        {icon}
        {label}
      </button>
      <AlertDialog.Content maxWidth="420px">
        <AlertDialog.Title>{title}</AlertDialog.Title>
        <AlertDialog.Description size="2">{description}</AlertDialog.Description>
        <div className="stg-confirm-actions">
          <AlertDialog.Cancel>
            <Button variant="soft" color="gray"><X size={14} aria-hidden="true" />Cancel</Button>
          </AlertDialog.Cancel>
          <AlertDialog.Action
            className="ghost small stg-danger"
            onClick={() => {
              setOpen(false);
              onConfirm();
            }}
          >
            <Button color="red">{icon}{confirmLabel}</Button>
          </AlertDialog.Action>
        </div>
      </AlertDialog.Content>
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

/** Blank means the server default; anything else must be a readable dollar amount. */
function spendLimitError(raw: string): string | null {
  const value = raw.trim();
  if (!value) return null;
  if (!/^\d+(?:\.\d{1,2})?$/.test(value) || !Number.isFinite(Number(value))) {
    return "Enter a dollar amount of 0 or more, with up to two decimals.";
  }
  return null;
}

function usd(value: string | number): string {
  return `$${Number(value).toFixed(2)}`;
}

function titleCase(value: string): string {
  return value.charAt(0).toUpperCase() + value.slice(1);
}

function ProfileSection({ settings }: { settings: ApiState<SettingsPayload> }) {
  return (
    <SettingsSection
      id="profile"
      title="Profile & floor"
      description="Set the details Fin-Agent uses when answering questions, and the balance the projection must not cross."
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
  return (
    <SettingsSection
      id="categories"
      title="Categories"
      description="Mark the categories an affordability tradeoff may suggest cutting, and review the merchant rules that assign them."
    >
      <Loadable
        state={tree}
        label="categories and rules"
        skeleton={<SettingsSkeleton label="Loading categories and rules…" rows={5} />}
      >
        {(data) => <CategoriesCard tree={data} onChanged={tree.reload} />}
      </Loadable>
    </SettingsSection>
  );
}

function CategoriesCard({ tree, onChanged }: { tree: CategoryTree; onChanged: () => void }) {
  const [merchant, setMerchant] = useState("");
  const [categoryId, setCategoryId] = useState("");
  const [ruleQuery, setRuleQuery] = useState("");
  const [rulePage, setRulePage] = useState(0);
  const [busyCategoryId, setBusyCategoryId] = useState<string | null>(null);
  const [editingCategoryId, setEditingCategoryId] = useState<string | null>(null);
  const [categoryName, setCategoryName] = useState("");
  const [editingRuleId, setEditingRuleId] = useState<string | null>(null);
  const [ruleCategoryId, setRuleCategoryId] = useState("");
  const [saving, setSaving] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);

  const activeCategories = tree.categories.filter((category) => category.active);
  const archivedCategories = tree.categories.filter((category) => !category.active);
  const selectedCategoryId =
    categoryId && activeCategories.some((category) => category.id === categoryId)
      ? categoryId
      : (activeCategories[0]?.id ?? "");
  const rules = [...tree.rules].sort(
    (a, b) => b.hits - a.hits || a.pattern.localeCompare(b.pattern),
  );
  const query = ruleQuery.trim().toLowerCase();
  const filteredRules = query
    ? rules.filter(
        (rule) =>
          rule.pattern.toLowerCase().includes(query) ||
          rule.category_name.toLowerCase().includes(query),
      )
    : rules;
  const pageCount = Math.max(1, Math.ceil(filteredRules.length / RULE_PAGE_SIZE));
  const currentPage = Math.min(rulePage, pageCount - 1);
  const visibleRules = filteredRules.slice(
    currentPage * RULE_PAGE_SIZE,
    (currentPage + 1) * RULE_PAGE_SIZE,
  );

  async function toggleReducible(category: CategoryTree["categories"][number]) {
    setBusyCategoryId(category.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({ reducible: !category.reducible }),
      });
      setMessage(
        category.reducible
          ? `“${category.name}” is protected from affordability cuts.`
          : `“${category.name}” may be suggested for cutting.`,
      );
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyCategoryId(null);
    }
  }

  async function saveCategoryName(category: CategoryTree["categories"][number]) {
    const name = categoryName.trim();
    if (!name) {
      setError("A category needs a name.");
      return;
    }
    if (name === category.name) {
      setEditingCategoryId(null);
      return;
    }
    setBusyCategoryId(category.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({ name }),
      });
      setEditingCategoryId(null);
      setMessage(`Renamed to “${name}”. Existing rows keep this category.`);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyCategoryId(null);
    }
  }

  async function setCategoryActive(
    category: CategoryTree["categories"][number],
    active: boolean,
  ) {
    setBusyCategoryId(category.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/categories/${category.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active }),
      });
      setMessage(
        active
          ? `“${category.name}” restored.`
          : `“${category.name}” archived. Existing rows and rules keep the label.`,
      );
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyCategoryId(null);
    }
  }

  async function addRule(event: FormEvent) {
    event.preventDefault();
    if (!merchant.trim() || !selectedCategoryId) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api("/api/category-rules", {
        method: "POST",
        body: JSON.stringify({
          pattern: merchant.trim(),
          category_id: selectedCategoryId,
          source: "user",
        }),
      });
      setMerchant("");
      setMessage("Rule saved. Your rules always take priority over an agent guess.");
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function saveRule(rule: CategoryTree["rules"][number]) {
    if (!ruleCategoryId) return;
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api("/api/category-rules", {
        method: "POST",
        body: JSON.stringify({
          pattern: rule.pattern,
          category_id: ruleCategoryId,
          source: "user",
        }),
      });
      setEditingRuleId(null);
      setMessage(
        rule.source === "llm"
          ? `“${rule.pattern}” is now your rule and beats the agent's guess.`
          : `“${rule.pattern}” now uses the selected category.`,
      );
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setSaving(false);
    }
  }

  async function removeRule(rule: CategoryTree["rules"][number]) {
    setError(null);
    setMessage(null);
    try {
      await api(`/api/category-rules/${rule.id}`, { method: "DELETE" });
      setMessage(`Rule for “${rule.pattern}” deleted. Past labels stay unchanged.`);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <div className="stg-card">
      <h4>Categories</h4>
      <p className="stg-hint">
        A reducible category may be suggested when an affordability tradeoff needs a cut. Renaming
        or archiving a category keeps its identity on existing rows.
      </p>
      <div className="stg-list">
        {activeCategories.map((category) => {
          const editing = editingCategoryId === category.id;
          return (
            <div className="stg-list-row" key={category.id}>
              <div className="stg-list-body">
                {editing ? (
                  <input
                    aria-label={`Name for ${category.name}`}
                    value={categoryName}
                    maxLength={100}
                    onChange={(event) => setCategoryName(event.target.value)}
                  />
                ) : (
                  <p className="stg-question">{category.name}</p>
                )}
                <p className="stg-list-meta">
                  {category.reducible
                    ? "Reducible · an affordability tradeoff may suggest cutting it."
                    : "Protected · not suggested for affordability cuts."}
                </p>
              </div>
              <div className="stg-list-actions">
                {editing ? (
                  <>
                    <button
                      type="button"
                      className="ghost small"
                      disabled={busyCategoryId === category.id || !categoryName.trim()}
                      onClick={() => void saveCategoryName(category)}
                    >
                      <Save size={14} strokeWidth={1.8} aria-hidden="true" />
                      Save
                    </button>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => setEditingCategoryId(null)}
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
                      aria-pressed={category.reducible}
                      disabled={busyCategoryId === category.id}
                      title="Toggle whether this category is reducible"
                      onClick={() => void toggleReducible(category)}
                    >
                      <Tag size={14} strokeWidth={1.8} aria-hidden="true" />
                      {category.reducible ? "Reducible" : "Protected"}
                    </button>
                    <button
                      type="button"
                      className="ghost small"
                      onClick={() => {
                        setEditingCategoryId(category.id);
                        setCategoryName(category.name);
                        setError(null);
                      }}
                    >
                      <Pencil size={14} strokeWidth={1.8} aria-hidden="true" />
                      Rename
                    </button>
                    <ConfirmButton
                      label="Archive"
                      icon={<Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />}
                      title={`Archive “${category.name}”?`}
                      description="It will not be offered for new movements, but existing rows and rules keep the label. You can restore it below."
                      confirmLabel="Archive category"
                      onConfirm={() => void setCategoryActive(category, false)}
                    />
                  </>
                )}
              </div>
            </div>
          );
        })}
      </div>

      {archivedCategories.length > 0 && (
        <>
          <div className="stg-subhead">
            <h4>Archived categories</h4>
            <span className="stg-hint">{archivedCategories.length} archived</span>
          </div>
          <p className="stg-hint">
            Archived categories stay on existing rows and rules. Restore one to offer it again.
          </p>
          <div className="stg-list">
            {archivedCategories.map((category) => (
              <div className="stg-list-row" key={category.id}>
                <div className="stg-list-body">
                  <p className="stg-question">{category.name}</p>
                </div>
                <div className="stg-list-actions">
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busyCategoryId === category.id}
                    onClick={() => void setCategoryActive(category, true)}
                  >
                    <RotateCcw size={14} strokeWidth={1.8} aria-hidden="true" />
                    Restore
                  </button>
                </div>
              </div>
            ))}
          </div>
        </>
      )}

      <div className="stg-subhead">
        <h4>Merchant rules</h4>
        {rules.length > 0 && <span className="stg-hint">{rules.length} rules · most used first</span>}
      </div>
      <p className="stg-hint">
        Your rules win over the agent&apos;s guesses. Agent rules are guesses the categorizer saved;
        changing one makes it your rule. Past labels are not rewritten.
      </p>

      {rules.length > 0 && (
        <div className="stg-filter">
          <label>
            Filter rules
            <input
              type="search"
              value={ruleQuery}
              placeholder="Merchant or category"
              onChange={(event) => {
                setRuleQuery(event.target.value);
                setRulePage(0);
              }}
            />
          </label>
        </div>
      )}

      {filteredRules.length === 0 ? (
        <div className="stg-empty">
          <p>
            {rules.length === 0
              ? "No rules yet. Add a user rule below; it will apply to future captures."
              : `No rules match “${ruleQuery.trim()}”.`}
          </p>
        </div>
      ) : (
        <>
          <p className="stg-scroll-hint">Scroll the table sideways to see every column.</p>
          <TableRegion label="Merchant category rules">
            <table>
              <thead>
                <tr>
                  <th>Merchant</th>
                  <th>Category</th>
                  <th>Source</th>
                  <th>Hits</th>
                  <th>
                    <span className="sr-only">Actions</span>
                  </th>
                </tr>
              </thead>
              <tbody>
                {visibleRules.map((rule) => (
                  <tr key={rule.id}>
                    <td>{rule.pattern}</td>
                    <td>
                      {editingRuleId === rule.id ? (
                        <select
                          aria-label={`Category for ${rule.pattern}`}
                          value={ruleCategoryId}
                          onChange={(event) => setRuleCategoryId(event.target.value)}
                        >
                          {tree.categories
                            .filter(
                              (category) => category.active || category.id === rule.category_id,
                            )
                            .map((category) => (
                              <option key={category.id} value={category.id}>
                                {category.name}
                                {category.active ? "" : " (archived)"}
                              </option>
                            ))}
                        </select>
                      ) : (
                        rule.category_name
                      )}
                    </td>
                    <td>
                      <span className={`badge ${rule.source === "user" ? "muted" : "warn"}`}>
                        {rule.source === "user" ? "You" : rule.source === "llm" ? "Agent" : "Seed"}
                      </span>
                    </td>
                    <td className="num">{rule.hits}</td>
                    <td>
                      <div className="stg-list-actions">
                        {editingRuleId === rule.id ? (
                          <>
                            <button
                              type="button"
                              className="ghost small"
                              disabled={saving || !ruleCategoryId}
                              onClick={() => void saveRule(rule)}
                            >
                              <Save size={14} strokeWidth={1.8} aria-hidden="true" />
                              Save
                            </button>
                            <button
                              type="button"
                              className="ghost small"
                              onClick={() => setEditingRuleId(null)}
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
                              onClick={() => {
                                setEditingRuleId(rule.id);
                                setRuleCategoryId(rule.category_id);
                                setError(null);
                              }}
                            >
                              <Pencil size={14} strokeWidth={1.8} aria-hidden="true" />
                              Edit
                            </button>
                            <ConfirmButton
                              label="Delete"
                              icon={<Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />}
                              title={`Delete the rule for “${rule.pattern}”?`}
                              description="Future captures stop using this rule. Labels already on past movements stay unchanged."
                              confirmLabel="Delete rule"
                              onConfirm={() => void removeRule(rule)}
                            />
                          </>
                        )}
                      </div>
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
          <Pager
            page={currentPage}
            pageCount={pageCount}
            total={filteredRules.length}
            pageSize={RULE_PAGE_SIZE}
            onPage={setRulePage}
          />
        </>
      )}

      <form className="stg-inline" onSubmit={(event) => void addRule(event)}>
        <label>
          Merchant
          <input
            value={merchant}
            placeholder="Swiggy"
            required
            onChange={(event) => setMerchant(event.target.value)}
          />
        </label>
        <label>
          Category
          <select
            value={selectedCategoryId}
            required
            onChange={(event) => setCategoryId(event.target.value)}
          >
            {activeCategories.map((category) => (
              <option key={category.id} value={category.id}>
                {category.name}
              </option>
            ))}
          </select>
        </label>
        <button type="submit" disabled={saving || !merchant.trim() || !selectedCategoryId}>
          {saving ? (
            <>
              <Busy />
              Saving…
            </>
          ) : (
            <>
              <Plus size={15} strokeWidth={1.8} aria-hidden="true" />
              Add user rule
            </>
          )}
        </button>
      </form>

      <Feedback error={error} message={message} />
    </div>
  );
}

function NotificationsSection({ settings }: { settings: ApiState<SettingsPayload> }) {
  return (
    <SettingsSection
      id="notifications"
      title="Notifications"
      description="Choose when reminders arrive and whether this browser may show a push notice."
    >
      <div className="stg-columns">
        <Loadable
          state={settings}
          label="notification settings"
          skeleton={<SettingsSkeleton label="Loading notification settings…" rows={2} />}
        >
          {(data) => <NotificationsCard settings={data} onSaved={settings.reload} />}
        </Loadable>
        <PushCard />
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

function PushCard() {
  const [supported] = useState(() => pushSupported());
  const [permission, setPermission] = useState<NotificationPermission | "unsupported">(
    "default",
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

  const permissionLabel =
    permission === "granted"
      ? "Allowed"
      : permission === "denied"
        ? "Blocked in this browser"
        : permission === "unsupported"
          ? "Not supported"
          : "Not asked yet";

  return (
    <div className="stg-card">
      <h3>Web push</h3>
      <p className="stg-hint">
        Push only says that something is ready, such as “Your weekly briefing is ready”. No amount,
        merchant, or balance is sent through the browser vendor's push service; the full item stays
        in the inbox.
      </p>
      {!supported ? (
        <div className="stg-empty">
          <p>This browser does not support web push.</p>
        </div>
      ) : (
        <>
          <p className="stg-status-line">
            <span className={`stg-status ${permission === "denied" ? "bad" : ""}`}>
              <Bell size={14} strokeWidth={1.8} aria-hidden="true" />
              {permissionLabel}
            </span>
            <span className="stg-status">
              {subscribed ? (
                <BellRing size={14} strokeWidth={1.8} aria-hidden="true" />
              ) : (
                <BellOff size={14} strokeWidth={1.8} aria-hidden="true" />
              )}
              {subscribed ? "Subscribed" : "Not subscribed"}
            </span>
          </p>
          {permission === "denied" && (
            <p className="stg-hint">
              Allow notifications for this site in your browser settings, then try again.
            </p>
          )}
          <div className="stg-actions">
            <button type="button" className="ghost" disabled={busy} onClick={() => void toggle()}>
              {busy ? (
                <>
                  <Busy />
                  Working…
                </>
              ) : subscribed ? (
                <>
                  <BellOff size={15} strokeWidth={1.8} aria-hidden="true" />
                  Disable push notifications
                </>
              ) : (
                <>
                  <BellRing size={15} strokeWidth={1.8} aria-hidden="true" />
                  Enable push notifications
                </>
              )}
            </button>
          </div>
        </>
      )}
      <Feedback error={error} message={null} />
    </div>
  );
}

function PasskeysSection() {
  return (
    <SettingsSection
      id="passkeys"
      title="Passkeys"
      description="Signing in and the devices that can unlock this instance. Every sensitive change asks for a fresh passkey check."
    >
      <div className="stg-card stg-passkeys">
        <PasskeysCard />
      </div>
    </SettingsSection>
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
  const [spend, setSpend] = useState("");
  const [spendError, setSpendError] = useState<string | null>(null);
  const [editingId, setEditingId] = useState<string | null>(null);
  const [editPrompt, setEditPrompt] = useState("");
  const [editRule, setEditRule] = useState("");
  const [editSpend, setEditSpend] = useState("");
  const [editSpendError, setEditSpendError] = useState<string | null>(null);
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
    const invalidSpend = spendLimitError(spend);
    if (invalidSpend) {
      setSpendError(invalidSpend);
      return;
    }
    setSpendError(null);
    setSaving(true);
    setError(null);
    setMessage(null);
    try {
      await api("/api/scheduled-questions", {
        method: "POST",
        body: JSON.stringify({
          prompt: prompt.trim(),
          rule: rule.trim(),
          spend_limit_usd: spend.trim() === "" ? null : Number(spend),
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
    setEditSpend(question.spend_limit_usd ?? "");
    setEditSpendError(null);
    setError(null);
    setMessage(null);
  }

  async function saveEdit(question: ScheduledQuestion) {
    if (!editPrompt.trim() || !editRule.trim()) return;
    const invalidSpend = spendLimitError(editSpend);
    if (invalidSpend) {
      setEditSpendError(invalidSpend);
      return;
    }
    setBusyId(question.id);
    setError(null);
    setMessage(null);
    try {
      await api(`/api/scheduled-questions/${question.id}`, {
        method: "PATCH",
        body: JSON.stringify({
          prompt: editPrompt.trim(),
          rule: editRule.trim(),
          ...(editSpend.trim() === "" ? {} : { spend_limit_usd: Number(editSpend) }),
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

  const spendDescriptionIds = spendError ? "stg-spend-hint stg-spend-error" : "stg-spend-hint";

  return (
    <div className="stg-card">
      <p className="stg-hint">
        Each run is read-only, uses the pinned financial snapshot, and stops after 20 model
        requests or its spend limit. The instance also shares a $20 daily model ceiling.
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
                          <label>
                            Spend limit per run (USD)
                            <input
                              inputMode="decimal"
                              value={editSpend}
                              placeholder={data.default_spend_limit_usd}
                              aria-invalid={Boolean(editSpendError)}
                              aria-describedby={`stg-edit-spend-hint-${question.id}`}
                              onChange={(event) => {
                                setEditSpend(event.target.value);
                                if (editSpendError) {
                                  setEditSpendError(spendLimitError(event.target.value));
                                }
                              }}
                              onBlur={() => setEditSpendError(spendLimitError(editSpend))}
                            />
                          </label>
                        </div>
                        <p id={`stg-edit-rule-hint-${question.id}`} className="stg-hint">
                          Times follow your notification time. Blank spend keeps the current limit.
                        </p>
                        {editSpendError && (
                          <p
                            id={`stg-edit-spend-hint-${question.id}`}
                            className="error"
                            role="alert"
                          >
                            {editSpendError}
                          </p>
                        )}
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
                          · limit{" "}
                          {question.spend_limit_usd != null
                            ? usd(question.spend_limit_usd)
                            : `default ${usd(data.default_spend_limit_usd)}`}
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
          <label>
            Spend limit per run (USD)
            <input
              inputMode="decimal"
              value={spend}
              placeholder={data.default_spend_limit_usd}
              aria-invalid={Boolean(spendError)}
              aria-describedby={spendDescriptionIds}
              onChange={(event) => {
                setSpend(event.target.value);
                if (spendError) setSpendError(spendLimitError(event.target.value));
              }}
              onBlur={() => setSpendError(spendLimitError(spend))}
            />
          </label>
        </div>
        <p id="stg-rule-hint" className="stg-hint">
          Use an RRULE such as <code>FREQ=WEEKLY;BYDAY=SU</code>. Times follow your notification
          time.
        </p>
        <p id="stg-spend-hint" className="stg-hint">
          Leave blank to use the default ${data.default_spend_limit_usd} per run. A blank field
          means the default, not zero. Enter 0 or more with up to two decimals.
        </p>
        {spendError && (
          <p id="stg-spend-error" className="error" role="alert">
            {spendError}
          </p>
        )}
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

function JobsSection() {
  const jobs = useApi<JobStatus>("/api/jobs");
  return (
    <SettingsSection
      id="jobs"
      title="Jobs"
      description="The one loop in the API process runs recurring schedules. A missed run fires once when the machine wakes."
    >
      <Loadable
        state={jobs}
        label="jobs"
        skeleton={<SettingsSkeleton label="Loading jobs…" rows={5} />}
      >
        {(data) => <JobsCard data={data} />}
      </Loadable>
      <div className="stg-card">
        <h4>Models and diagnostics</h4>
        <RuntimeInfo />
      </div>
    </SettingsSection>
  );
}

function JobsCard({ data }: { data: JobStatus }) {
  const counts = Object.entries(data.counts);
  return (
    <div className="stg-card">
      <div className="stg-status-line">
        {counts.length === 0 ? (
          <span className="badge muted">No job runs recorded yet</span>
        ) : (
          counts.map(([status, count]) => (
            <span key={status} className={`badge ${status === "failed" ? "danger" : "muted"}`}>
              {count} {titleCase(status)}
            </span>
          ))
        )}
      </div>

      <div className="stg-subhead">
        <h4>Schedules</h4>
        <span className="stg-hint">{data.schedules.length} configured</span>
      </div>
      {data.schedules.length === 0 ? (
        <div className="stg-empty">
          <p>No schedules are configured.</p>
        </div>
      ) : (
        <>
          <p className="stg-scroll-hint">Scroll the table sideways to see every column.</p>
          <TableRegion label="Job schedules">
            <table>
              <thead>
                <tr>
                  <th>Schedule</th>
                  <th>Rule</th>
                  <th>Status</th>
                  <th>Next</th>
                  <th>Last</th>
                </tr>
              </thead>
              <tbody>
                {data.schedules.map((schedule) => (
                  <tr key={schedule.id} className={schedule.active ? "" : "muted-row"}>
                    <td>{schedule.label}</td>
                    <td className="fine">{schedule.rule}</td>
                    <td>
                      <span className={`badge ${schedule.active ? "good" : "muted"}`}>
                        {schedule.active ? "Active" : "Paused"}
                      </span>
                    </td>
                    <td>{formatMoment(schedule.next_run_at)}</td>
                    <td>{formatMoment(schedule.last_run_at)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        </>
      )}

      <div className="stg-subhead">
        <h4>Recent runs</h4>
        <span className="stg-hint">{data.recent.length} shown</span>
      </div>
      {data.recent.length === 0 ? (
        <div className="stg-empty">
          <p>No job runs have been recorded.</p>
        </div>
      ) : (
        <>
          <p className="stg-scroll-hint">Scroll the table sideways to see every column.</p>
          <TableRegion label="Recent job runs">
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
                {data.recent.map((job) => (
                  <tr key={job.id}>
                    <td>{titleCase(job.kind.replaceAll("_", " "))}</td>
                    <td>
                      <span
                        className={`badge ${
                          job.status === "failed"
                            ? "danger"
                            : job.status === "done"
                              ? "good"
                              : job.status === "running"
                                ? "warn"
                                : "muted"
                        }`}
                      >
                        {titleCase(job.status)}
                      </span>
                    </td>
                    <td className="num">{job.attempts}</td>
                    <td>{formatMoment(job.finished_at)}</td>
                    <td className="fine">{job.last_error ?? ""}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </TableRegion>
        </>
      )}
    </div>
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
          Restore runs on the host, not from this page. <code>fin-agent restore</code> restores the
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
    description: "Every table and record in one readable file. Best for inspecting or moving your data.",
    path: "/api/export/json",
    filename: "fin-agent-export.json",
    action: "Download JSON",
    Icon: FileJson,
  },
  {
    id: "csv",
    title: "CSV ZIP",
    description: "One spreadsheet per table, packed in a ZIP. Best for analysis in a spreadsheet app.",
    path: "/api/export/csv",
    filename: "fin-agent-export.zip",
    action: "Download CSV ZIP",
    Icon: FileSpreadsheet,
  },
  {
    id: "dump",
    title: "Database dump",
    description: "The complete PostgreSQL database as SQL. Best for a full recovery or migration.",
    path: "/api/export/pg_dump",
    filename: "fin-agent-export.sql",
    action: "Download database dump",
    Icon: Database,
  },
] as const;

function ExportSection() {
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);

  async function download(format: (typeof EXPORT_FORMATS)[number]) {
    setBusy(format.id);
    setError(null);
    try {
      await ensureFreshPasskey();
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
      description="Download your data in the form that fits the job. Every export asks for a fresh passkey check."
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

        <p className="stg-export-note">
          <ShieldCheck size={16} strokeWidth={1.8} aria-hidden="true" />
          The file is built on this machine and only leaves when your browser saves it. The restic
          backup sidecar is separate; its latest status is in Backups.
        </p>

        <Feedback error={error} message={null} />
      </div>
    </SettingsSection>
  );
}

function MemorySection({ preferences }: { preferences: ApiState<PreferenceList> }) {
  return (
    <SettingsSection
      id="memory"
      title="Agent memory"
      description="Saved preferences injected into answers on this instance. Turning one off keeps it listed and reversible."
    >
      <Loadable
        state={preferences}
        label="agent memory"
        skeleton={<SettingsSkeleton label="Loading agent memory…" rows={4} />}
      >
        {(data) => <MemoryCard data={data} onChanged={preferences.reload} />}
      </Loadable>
    </SettingsSection>
  );
}

function MemoryCard({ data, onChanged }: { data: PreferenceList; onChanged: () => void }) {
  const [query, setQuery] = useState("");
  const [source, setSource] = useState<"all" | "agent" | "user">("all");
  const [page, setPage] = useState(0);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [editing, setEditing] = useState<{ id: string; body: string } | null>(null);
  const [error, setError] = useState<string | null>(null);

  const needle = query.trim().toLowerCase();
  const filtered = data.preferences.filter((item) => {
    if (source !== "all" && item.created_by !== source) return false;
    return !needle || item.body.toLowerCase().includes(needle);
  });
  const pageCount = Math.max(1, Math.ceil(filtered.length / MEMORY_PAGE_SIZE));
  const currentPage = Math.min(page, pageCount - 1);
  const visible = filtered.slice(
    currentPage * MEMORY_PAGE_SIZE,
    (currentPage + 1) * MEMORY_PAGE_SIZE,
  );

  async function toggle(item: Preference) {
    setBusyId(item.id);
    setError(null);
    try {
      await api(`/api/preferences/${item.id}`, {
        method: "PATCH",
        body: JSON.stringify({ active: !item.active }),
      });
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  async function saveEdit(item: Preference) {
    const text = editing && editing.id === item.id ? editing.body.trim() : "";
    if (!text) {
      setError("A memory cannot be empty.");
      return;
    }
    setBusyId(item.id);
    setError(null);
    try {
      await api(`/api/preferences/${item.id}/content`, {
        method: "PATCH",
        body: JSON.stringify({ body: text }),
      });
      setEditing(null);
      onChanged();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusyId(null);
    }
  }

  async function remove(item: Preference) {
    setBusyId(item.id);
    setError(null);
    try {
      await api(`/api/preferences/${item.id}`, { method: "DELETE" });
      if (editing?.id === item.id) setEditing(null);
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
        The agent saves these unprompted with a card and undo. Turning one off keeps it listed and
        reversible; deleting it for good blanks the text in the audit log and cannot be undone.
      </p>

      {data.preferences.length > 0 && (
        <div className="stg-filter">
          <label>
            Filter memory
            <input
              type="search"
              value={query}
              placeholder="Search saved text"
              onChange={(event) => {
                setQuery(event.target.value);
                setPage(0);
              }}
            />
          </label>
          <label>
            Saved by
            <select
              value={source}
              onChange={(event) => {
                setSource(event.target.value as "all" | "agent" | "user");
                setPage(0);
              }}
            >
              <option value="all">Anyone</option>
              <option value="agent">The agent</option>
              <option value="user">You</option>
            </select>
          </label>
        </div>
      )}

      {data.preferences.length === 0 ? (
        <div className="stg-empty">
          <p>Nothing remembered yet. The agent will add memories as you use it.</p>
        </div>
      ) : filtered.length === 0 ? (
        <div className="stg-empty">
          <p>No memories match these filters.</p>
        </div>
      ) : (
        <>
          <div className="stg-list">
            {visible.map((item) => (
              <div className={item.active ? "stg-list-row" : "stg-list-row off"} key={item.id}>
                <div className="stg-list-body">
                  {editing?.id === item.id ? (
                    <>
                      <input
                        aria-label="Memory text"
                        value={editing.body}
                        maxLength={MEMORY_MAX_LENGTH}
                        autoFocus
                        onChange={(event) =>
                          setEditing({ id: item.id, body: event.target.value })
                        }
                        onKeyDown={(event) => {
                          if (event.key === "Escape") {
                            setEditing(null);
                            setError(null);
                          }
                          if (event.key === "Enter") void saveEdit(item);
                        }}
                      />
                      <div className="stg-actions">
                        <button
                          type="button"
                          className="ghost small"
                          disabled={busyId === item.id}
                          onClick={() => void saveEdit(item)}
                        >
                          <Save size={14} strokeWidth={1.8} aria-hidden="true" />
                          Save
                        </button>
                        <button
                          type="button"
                          className="ghost small"
                          disabled={busyId === item.id}
                          onClick={() => {
                            setEditing(null);
                            setError(null);
                          }}
                        >
                          <X size={14} strokeWidth={1.8} aria-hidden="true" />
                          Cancel
                        </button>
                      </div>
                    </>
                  ) : (
                    <p className="stg-memory-body">{item.body || "(deleted)"}</p>
                  )}
                  <p className="stg-list-meta">
                    Saved preference · This instance ·{" "}
                    {item.created_by === "agent" ? "Saved by the agent" : "Saved by you"} ·{" "}
                    {formatMoment(item.created_at)} · {item.active ? "on" : "off"}
                  </p>
                </div>
                <div className="stg-list-actions">
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busyId === item.id}
                    onClick={() => {
                      setEditing({ id: item.id, body: item.body });
                      setError(null);
                    }}
                  >
                    <Pencil size={14} strokeWidth={1.8} aria-hidden="true" />
                    Edit
                  </button>
                  <button
                    type="button"
                    className="ghost small"
                    disabled={busyId === item.id}
                    onClick={() => void toggle(item)}
                  >
                    {item.active ? (
                      <PowerOff size={14} strokeWidth={1.8} aria-hidden="true" />
                    ) : (
                      <Power size={14} strokeWidth={1.8} aria-hidden="true" />
                    )}
                    {item.active ? "Turn off" : "Turn on"}
                  </button>
                  <ConfirmButton
                    label="Delete for good"
                    icon={<Trash2 size={14} strokeWidth={1.8} aria-hidden="true" />}
                    title="Delete this memory for good?"
                    description="Its text is blanked in the audit log and this cannot be undone."
                    confirmLabel="Delete for good"
                    onConfirm={() => void remove(item)}
                  />
                </div>
              </div>
            ))}
          </div>
          <Pager
            page={currentPage}
            pageCount={pageCount}
            total={filtered.length}
            pageSize={MEMORY_PAGE_SIZE}
            onPage={setPage}
          />
        </>
      )}

      <Feedback error={error} message={null} />
    </div>
  );
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
      case "passkeys":
        return <PasskeysSection />;
      case "questions":
        return <ScheduledQuestionsSection />;
      case "jobs":
        return <JobsSection />;
      case "backups":
        return <BackupsSection backup={backup} />;
      case "export":
        return <ExportSection />;
      case "memory":
        return <MemorySection preferences={preferences} />;
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
