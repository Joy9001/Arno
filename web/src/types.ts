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
  card_outstanding_paise?: number;
  available_credit_paise?: number;
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
  card_owed_paise: number;
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
  merchant_pattern: string;
  category_id: string;
  category_name: string;
  source: "user" | "llm";
  hits: number;
};

export type CategoryTree = {
  categories: Category[];
  rules: Rule[];
  as_of: string;
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
  counterparty: string | null;
  suggested_category: { category_id: string; category_name: string; source: string } | null;
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

export type JobRunStatus = {
  id: string;
  key: string;
  kind: string;
  status: "pending" | "running" | "done" | "failed";
  attempts: number;
  run_at: string;
  finished_at: string | null;
  last_error: string | null;
};

export type JobStatus = {
  schedules: JobScheduleStatus[];
  recent: JobRunStatus[];
  counts: Record<string, number>;
  as_of: string;
};

export type PushKey = {
  public_key: string;
  subject: string;
};
