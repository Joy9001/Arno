export type Account = {
  id: string;
  kind: "bank" | "card" | "cash" | "wallet" | "loan" | "asset" | "goal" | "person";
  name: string;
  institution: string | null;
  last4: string | null;
  liquid: boolean;
  active: boolean;
  balance_paise: number;
  balance_as_of: string | null;
  statement_balance_paise: number | null;
  statement_balance_as_of: string | null;
  statement_age_days: number | null;
  credit_limit_paise: number | null;
  statement_day: number | null;
  due_day: number | null;
  rate_bps: number | null;
  tenure_months: number | null;
  card_outstanding_paise?: number | null;
  available_credit_paise?: number | null;
  loan_outstanding_paise?: number;
};

export type CashQuality = {
  status: "known" | "unknown" | "no_accounts";
  known_paise: number;
  known_count: number;
  unknown_count: number;
  unknown_accounts: string[];
};

export type AccountSummary = {
  accounts: Account[];
  liquid_total_paise: number;
  card_owed_paise: number | null;
  loan_outstanding_paise: number;
  cash_quality: CashQuality;
  as_of: string;
};

export type Transfer = {
  transfer_id: string;
  from_account_id: string;
  from_name: string;
  from_kind: string;
  to_account_id: string;
  to_name: string;
  to_kind: string;
  amount_paise: number;
  category_id: string | null;
  category_name: string | null;
  payee_id: string | null;
  payee_name: string | null;
  expected_date: string | null;
  note: string | null;
};

export type Movement = {
  id: string;
  date: string;
  kind: string;
  description: string;
  reverses_id: string | null;
  reversed_by: string | null;
  reversed: boolean;
  amount_paise: number;
  category_name: string | null;
  payee_name: string | null;
  transfers: Transfer[];
  evidence: string[];
  undo_id: string | null;
};

export type MovementList = {
  items: Movement[];
  total_count: number;
  next_cursor: string | null;
  as_of: string;
};

export type Schedule = {
  id: string;
  kind: string;
  subtype: string | null;
  name: string;
  amount_paise: number | null;
  account_id: string | null;
  account_name: string | null;
  category_id: string | null;
  category_name: string | null;
  rule: string;
  holiday_adjust: "none" | "next" | "previous";
  confirmed: boolean;
  starts: string;
  ends: string | null;
  active: boolean;
  outside_text: boolean;
  next_date: string | null;
};

export type UpcomingEvent = {
  date: string;
  schedule_id: string | null;
  name: string;
  kind: string;
  subtype: string | null;
  amount_paise: number | null;
  occurrence_id?: string | null;
  paid_paise?: number | null;
  remaining_paise?: number | null;
  payment_status?: "scheduled" | "unmatched" | "partial" | "paid" | "recorded" | "untracked";
  payments?: Array<{
    id: string;
    movement_id: string;
    date: string;
    description: string | null;
    amount_paise: number;
    active: boolean;
  }>;
  confirmed: boolean;
  account_id: string | null;
  account_name: string | null;
  category_id: string | null;
  category_name: string | null;
};

export type Upcoming = {
  events: UpcomingEvent[];
  from: string;
  to: string;
  as_of: string;
};

export type Goal = {
  id: string;
  name: string;
  target_paise: number;
  saved_paise: number;
  remaining_paise: number;
  progress: number;
  deadline: string | null;
  priority: number;
  account_id: string;
  contribution_schedule_id: string | null;
  status: string;
  created_at: string | null;
};

export type Person = {
  payee_id: string;
  display_name: string;
  kind: string;
  upi_id: string | null;
  default_category_id: string | null;
  account_id: string | null;
  balance_paise: number;
  direction: "owes_you" | "you_owe" | "settled";
  next_expected_date: string | null;
  account_active: boolean | null;
};

export type Settings = {
  base_currency: string;
  time_zone: string;
  floor_paise: number | null;
  default_horizon_days: number;
  notification_time: string;
  weekly_briefing_dow: number;
  reminder_lead_days: number;
  profile: { user_id: string | null; display_name: string | null };
  last_model_error_type: string | null;
  last_model_error_at: string | null;
  updated_at: string | null;
};

export type Category = {
  id: string;
  name: string;
  reducible: boolean;
  active: boolean;
};

export type Rule = {
  id: string;
  match: "equals" | "contains" | "regex";
  pattern: string;
  category_id: string;
  category_name: string;
  source: "user" | "llm" | "seed";
  priority: number;
  hits: number;
};

export type CategoryTree = {
  categories: Category[];
  rules: Rule[];
  as_of: string;
};

export type UncategorizedGroup = {
  key: string;
  kind: "merchant" | "person";
  count: number;
  total_paise: number;
  narration: string;
  hint: { category_id: string | null; reason: string | null } | null;
};

export type UncategorizedList = {
  groups: UncategorizedGroup[];
  total: number;
};

export type BackupStatus = {
  last_success_at: string | null;
  last_success_age_seconds: number | null;
  last_attempt: {
    id: string;
    status: "success" | "failed";
    started_at: string;
    finished_at: string | null;
    dump_bytes: number | null;
    duration_ms: number | null;
    restic_snapshot: string | null;
    error: string | null;
  } | null;
  due_at: string | null;
  due: boolean;
  alert_at: string | null;
  overdue: boolean;
  checked_at: string;
};

export type User = {
  id: string;
  display_name: string;
};

export type Passkey = {
  id: string;
  name: string;
  created_at: string;
  last_used_at: string | null;
};

export type Enrollment = {
  id: string;
  link: string;
  expires_at: string;
  kind: "add_device";
};

export type ImportCounts = {
  new: number;
  certain: number;
  ambiguous: number;
  ignored: number;
  logged_not_on_statement: number;
};

export type ImportCandidate = {
  movement_id: string;
  date: string;
  description: string;
  amount_paise: number;
  kind: string;
};

export type ImportLine = {
  id: string;
  date: string;
  description: string;
  amount_paise: number;
  debit_paise: number | null;
  credit_paise: number | null;
  balance_paise: number | null;
  upi_ref: string | null;
  match_state: "new" | "certain" | "ambiguous" | "merged" | "ignored";
  movement_id: string | null;
  /** The matched ledger row's own date, when this line's date will replace it. */
  ledger_date: string | null;
  counterparty: string | null;
  suggested_transfer_account_id: string | null;
  transfer_confirmed: boolean;
  credit_kind: "refund" | "transfer" | "income" | null;
  credit_source_account_id: string | null;
  credit_decision_required: boolean;
  candidates: ImportCandidate[];
};

export type ImportRecord = {
  import_id: string;
  status:
    | "parsing"
    | "needs_password"
    | "needs_input"
    | "review"
    | "not_a_statement"
    | "failed"
    | "paused"
    | "committed"
    | "rejected";
  attachment: {
    filename: string | null;
    content_type: string | null;
    size_bytes: number | null;
    sha256: string;
    purged_at: string | null;
  } | null;
  account: { id: string; name: string; kind: string } | null;
  period: { start: string | null; end: string | null } | null;
  opening_paise: number | null;
  closing_paise: number | null;
  check: "VERIFIED" | "DISCREPANCY" | "FAILED" | null;
  check_detail: Record<string, unknown> | null;
  opening_source: "manual" | "ledger" | "printed" | "parser-zero" | null;
  opening_evidenced: boolean;
  opening_required: boolean;
  /** The difference a commit would book as a balance adjustment, and the host's reason. */
  closing_adjustment_paise: number;
  closing_adjustment_reason: string | null;
  /** A stated balance the statement's own rows disagree with, and the host's sentence. */
  stated_balance_paise: number | null;
  stated_balance_as_of: string | null;
  stated_balance_reason: string | null;
  /** Why the closing came from the last running balance, when it did. */
  closing_source_note: string | null;
  counts: ImportCounts | null;
  review: {
    counts?: ImportCounts;
    ambiguous?: unknown[];
    logged_not_on_statement?: {
      movement_id: string;
      date: string;
      description: string;
      amount_paise: number;
    }[];
    adjustment_paise?: number | null;
  } | null;
  reason: string | null;
  parser_saved: boolean;
  /** Null until the import is committed, or when no categorization job exists. */
  categorize?: CategorizeState | null;
  created_at: string | null;
  committed_at: string | null;
  rejected_at: string | null;
  undone_at: string | null;
  lines: ImportLine[];
};

export type ImportList = {
  items: {
    import_id: string;
    status: ImportRecord["status"];
    filename: string | null;
    account: string | null;
    period: ImportRecord["period"];
    check: ImportRecord["check"];
    counts: ImportCounts | null;
    reason: string | null;
    created_at: string | null;
    committed_at: string | null;
    undone_at: string | null;
  }[];
};

export type NotificationItem = {
  id: string;
  kind: string;
  title: string;
  body: string | null;
  data: Record<string, unknown>;
  read_at: string | null;
  created_at: string;
};

export type NotificationList = {
  notifications: NotificationItem[];
  unread_count: number;
  as_of: string;
};

export type NotificationChat = {
  thread_id: string;
  title: string | null;
  message: string;
  outside_text: boolean;
};

export type ScheduledQuestion = {
  id: string;
  prompt: string;
  rule: string;
  spend_limit_usd: string | null;
  default_spend_limit_usd: string;
  last_run_at: string | null;
  next_run_at: string | null;
  active: boolean;
  created_at: string | null;
};

export type ScheduledQuestionList = {
  questions: ScheduledQuestion[];
  default_spend_limit_usd: string;
  as_of: string;
};

export type JobScheduleStatus = {
  id: string;
  kind: string;
  payload: Record<string, unknown>;
  label: string;
  rule: string;
  next_run_at: string;
  last_run_at: string | null;
  active: boolean;
};

export type JobView = {
  id: string;
  key: string;
  kind: string;
  status: "pending" | "running" | "done" | "failed";
  /** Failed attempts so far: 0 while the first attempt runs. */
  attempts: number;
  /** The 1-based attempt now running, or the last one that ran. */
  attempt: number;
  max_attempts: number;
  /** For pending jobs, when the next attempt starts. */
  run_at: string;
  created_at: string;
  started_at: string | null;
  finished_at: string | null;
  last_error: string | null;
  result: Record<string, unknown> | null;
  subject: string | null;
  import_id: string | null;
};

export type JobActivity = {
  active: JobView[];
  finished: JobView[];
  now: string;
};

export type CategorizeState = {
  state: "queued" | "running" | "retrying" | "waiting_on_batch" | "done" | "partial" | "failed";
  labeled: number | null;
  uncategorized: number | null;
  failed_payees: number | null;
  error: string | null;
  attempt: number;
  max_attempts: number;
  next_run_at: string | null;
  started_at: string | null;
  finished_at: string | null;
  job_id: string | null;
};

export type JobStatus = {
  schedules: JobScheduleStatus[];
  recent: JobView[];
  counts: Record<string, number>;
  as_of: string;
};

export type PushKey = {
  public_key: string;
  subject: string;
};

export type ImportTaskReport = {
  status: "ready" | "not_a_statement" | "blocked" | "gave_up";
  reason?: string | null;
  blocking?: string | null;
  imports?: {
    import_id: string;
    status: string;
    accounts: number;
    rows: number;
    debits: number;
    credits: number;
    reason?: string | null;
  }[];
};

export type ImportTaskPlanItem = {
  id: string;
  content: string;
  status: "pending" | "in_progress" | "completed" | "cancelled" | "blocked";
  active_form: string | null;
  parent_id: string | null;
};

export type ImportTaskImport = {
  import_id: string;
  filename: string | null;
  status: ImportRecord["status"];
  reason: string | null;
  progress: string | null;
  counts: ImportCounts | null;
};

export type ImportTaskMessagePart =
  | { type: "text"; text: string }
  | {
      type: "tool-call";
      toolCallId: string;
      toolName: string;
      args: Record<string, unknown>;
      argsText: string;
      result?: unknown;
    };

/** The parser's own conversation, as the task API serves it back. */
export type ImportTaskMessage = {
  id: string;
  role: "user" | "assistant";
  createdAt: string;
  content: ImportTaskMessagePart[];
};

export type ImportTask = {
  task_id: string;
  agent_name: string;
  state: "running" | "finished";
  outcome: string | null;
  started_at: number;
  finished_at: number | null;
  report: ImportTaskReport | null;
  question: { question_id: string | null; text: string } | null;
  plan: ImportTaskPlanItem[];
  imports: ImportTaskImport[];
  messages: ImportTaskMessage[];
  model: string | null;
  conversation_id: string;
  /** The chat's turn about this report: waiting to start, or being answered now. */
  follow_up: "waiting" | "replying" | null;
  run: RunView;
};

export type RunStepStatus = "ok" | "failed" | "running" | "cut_off";

export type RunStep = {
  id: string;
  stage: RunStage["key"];
  segment: number;
  /** Host-made from the calls the script made. */
  title: string;
  status: RunStepStatus;
  calls: { name: string; count: number; failed: boolean }[];
  error: string | null;
  /** The script's first comment line: the agent's own words. */
  purpose: string | null;
  /** The model's reasoning for this step: its own words. */
  reasoning: string | null;
  code: string | null;
  /** What the script printed or returned, cut at 4,000 characters. */
  result?: string | null;
  started_at: string;
  seconds: number | null;
};

export type RunStage = {
  key: "open" | "saved" | "read" | "submit" | "moves" | "report";
  title: string;
  status: "done" | "running" | "stopped" | "todo" | "not_reached";
  steps: string[];
  seconds: number;
};

export type RunSegment = {
  run_id: string;
  used: number;
  limit: number;
  started_at: string;
  ended_at: string | null;
  stop: "budget" | "error" | null;
};

export type RunView = {
  stages: RunStage[];
  steps: RunStep[];
  segments: RunSegment[];
  used: number;
  limit: number;
  stop: "budget" | "error" | null;
};

export type ParserVersion = {
  version: number;
  created_at: string;
  helper_version: string | null;
  active: boolean;
};

export type StatementLayout = {
  id: string;
  institution: string;
  kind: string;
  variants: string[];
  active_version: number | null;
  versions: ParserVersion[];
};

export type LayoutList = {
  items: StatementLayout[];
};

export type RecapMoney = {
  paise: number;
  text: string;
};

export type RecapPeriod = {
  id: string;
  kind: "day" | "week" | "month";
  title: string;
  start: string;
  end: string;
  complete: boolean;
  compared_with: { start: string; end: string; label: string };
  spent: RecapMoney | null;
  new: boolean;
};

export type RecapList = {
  as_of: string;
  periods: RecapPeriod[];
  unseen: number;
};

/** A total or an amount with the name it belongs to; `recurring` carries only a count. */
export type RecapFigure = Partial<RecapMoney> & {
  name?: string;
  count?: number;
  direction?: "up" | "down";
};

export type RecapStory = {
  key: "total" | "in_out" | "where" | "change" | "biggest" | "frequent" | "recurring";
  label: string;
  figure: RecapFigure;
  line: string;
  rows: { story: string; key: string | null } | null;
  bars?: {
    name: string;
    amount: RecapMoney;
    rows: { story: string; key: string } | null;
  }[];
  in?: RecapMoney;
  out?: RecapMoney;
  charges?: { merchant: string; amount: RecapMoney; every: string; last_date: string }[];
};

export type RecapDetail = {
  period: Omit<RecapPeriod, "spent" | "new">;
  stories: RecapStory[];
  coverage: { complete: boolean; gaps: string[]; from: string | null; to: string | null };
  outside_text: boolean;
};

export type RecapRow = {
  id: string;
  date: string;
  description: string;
  payee: string | null;
  category: string | null;
  amount_paise: number;
  kind?: string;
  outside_text?: boolean;
};

export type RecapRows = {
  period: Omit<RecapPeriod, "spent" | "new">;
  story: string;
  key: string | null;
  rows: RecapRow[];
  sum: RecapMoney;
};
