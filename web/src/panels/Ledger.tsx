import { useEffect, useState } from "react";
import * as Dialog from "@radix-ui/react-dialog";
import { Skeleton } from "@radix-ui/themes";
import {
  AlertCircle,
  ChevronLeft,
  ChevronRight,
  FileSearch,
  MessageCircle,
  RefreshCw,
  Undo2,
  X,
} from "lucide-react";
import { askAgent } from "../ask";
import { api, errorMessage } from "../api";
import { formatDate, formatPaise } from "../format";
import { Busy } from "../Loading";
import { movementLabel, movementNote, humanizeKind } from "../movement";
import type {
  AccountSummary,
  CategoryTree,
  Movement,
  MovementList,
  UncategorizedGroup,
  UncategorizedList,
} from "../types";
import { useApi } from "../useApi";
import "./Activity.css";

const PAGE_SIZE = 25;

const KINDS = [
  "expense",
  "income",
  "transfer",
  "split",
  "loan",
  "refund",
  "settlement",
  "adjustment",
];

type Filters = {
  kind: string;
  accountId: string;
  start: string;
  end: string;
  includeReversed: boolean;
};

const EMPTY: Filters = {
  kind: "",
  accountId: "",
  start: "",
  end: "",
  includeReversed: false,
};

type Request = {
  filters: Filters;
  cursors: Array<string | null>;
  index: number;
  version: number;
};

type Loaded = {
  items: Movement[];
  nextCursor: string | null;
  index: number;
};

type MovementSource = {
  kind: string;
  ref: string | null;
  message_id: string | null;
  conversation_id: string | null;
};

type MovementDetail = Movement & { sources?: MovementSource[] };

const THREAD_ID = /^[A-Za-z0-9_-]{1,120}$/;

/** Ask Shell to open the exact message that recorded this row; never guess a thread. */
function openConversation(threadId: string, messageId: string | null) {
  if (!THREAD_ID.test(threadId)) return;
  const detail =
    messageId && THREAD_ID.test(messageId) ? { threadId, messageId } : threadId;
  window.dispatchEvent(new CustomEvent("arno:open-thread", { detail }));
}

function buildParams(filters: Filters, cursor: string | null): string {
  const params = new URLSearchParams({
    limit: String(PAGE_SIZE),
    include_reversed: String(filters.includeReversed),
  });
  if (filters.kind) params.set("kind", filters.kind);
  if (filters.accountId) params.set("account_id", filters.accountId);
  if (filters.start) params.set("start", filters.start);
  if (filters.end) params.set("end", filters.end);
  if (cursor) params.set("cursor", cursor);
  return params.toString();
}

function matchesSearch(movement: Movement, query: string): boolean {
  const haystack = [
    movementLabel(movement),
    movement.description,
    movement.category_name ?? "",
    movement.payee_name ?? "",
    humanizeKind(movement.kind),
  ]
    .join(" ")
    .toLowerCase();
  return haystack.includes(query);
}

function MovementRow({
  movement,
  onOpen,
}: {
  movement: Movement;
  onOpen: (movement: Movement) => void;
}) {
  const category =
    movement.kind === "transfer" ? "Transfer" : (movement.category_name ?? "—");
  return (
    <article className="act-move">
      <div className="act-move-date">{formatDate(movement.date)}</div>
      <div className="act-move-main">
        <strong title={movement.description}>{movementLabel(movement)}</strong>
        <span className="fine">
          {movement.kind === "transfer" ? movementNote(movement) : humanizeKind(movement.kind)}
        </span>
      </div>
      <div className="act-move-cat" title={category}>
        {category}
      </div>
      <div className="act-move-amount">
        <strong>{formatPaise(movement.amount_paise)}</strong>
        <span className={`badge ${movement.reversed ? "danger" : "muted"}`}>
          {movement.reversed ? "Reversed" : humanizeKind(movement.kind)}
        </span>
      </div>
      <div className="act-move-open">
        <button
          type="button"
          className="ghost small"
          onClick={() => onOpen(movement)}
          aria-label={`Details for ${movementLabel(movement)}`}
        >
          <FileSearch size={14} aria-hidden="true" />
          Details
        </button>
      </div>
    </article>
  );
}

function LedgerSkeleton() {
  return (
    <div className="act-skel" aria-busy="true" aria-label="Loading movements">
      {[0, 1, 2, 3, 4, 5].map((row) => (
        <div className="act-skel-row" key={row}>
          <Skeleton className="sk act-skel-line short" />
          <div className="act-skel-stack">
            <Skeleton className="sk act-skel-line" />
            <Skeleton className="sk act-skel-line tiny" />
          </div>
          <Skeleton className="sk act-skel-line short" />
          <Skeleton className="sk act-skel-line amount" />
          <Skeleton className="sk act-skel-line button" />
        </div>
      ))}
      <span className="sr-only">Loading movements…</span>
    </div>
  );
}

// The queue can run to hundreds of payees; the ledger below must stay in reach.
const UNCATEGORIZED_PREVIEW = 4;

function UncategorizedSection({
  onAnswered,
  version,
}: {
  onAnswered: () => void;
  version: number;
}) {
  const list = useApi<UncategorizedList>(`/api/uncategorized?v=${version}`);
  const [expanded, setExpanded] = useState(false);
  const tree = useApi<CategoryTree>("/api/categories");
  const [choice, setChoice] = useState<Record<string, string>>({});
  const [busy, setBusy] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [message, setMessage] = useState<string | null>(null);
  const [undoId, setUndoId] = useState<string | null>(null);
  const activeCategories = (tree.data?.categories ?? []).filter((item) => item.active);

  async function answer(group: UncategorizedGroup) {
    const categoryId = choice[group.key];
    if (!categoryId || busy) return;
    setBusy(group.key);
    setError(null);
    setMessage(null);
    setUndoId(null);
    try {
      const result = await api<{ labeled: number; undo_id: string }>(
        "/api/uncategorized/answer",
        {
          method: "POST",
          body: JSON.stringify({ key: group.key, category_id: categoryId }),
        },
      );
      setUndoId(result.undo_id);
      setMessage(
        `${result.labeled} ${result.labeled === 1 ? "row" : "rows"} labeled. One undo reverts it.`,
      );
      list.reload();
      tree.reload();
      onAnswered();
    } catch (caught) {
      setError(errorMessage(caught));
    } finally {
      setBusy(null);
    }
  }

  async function undoAnswer() {
    if (!undoId) return;
    setError(null);
    try {
      await api(`/api/undo/${undoId}`, { method: "POST" });
      setUndoId(null);
      setMessage("The answer was undone.");
      list.reload();
      tree.reload();
      onAnswered();
    } catch (caught) {
      setError(errorMessage(caught));
    }
  }

  return (
    <section className="act-uncat" aria-labelledby="uncat-title">
      <div className="act-uncat-head">
        <h3 id="uncat-title">Uncategorized ({list.data?.total ?? 0})</h3>
      </div>
      <p className="muted">
        Rows the rules and the agent were not sure about. Answer a payee once and its future rows
        use the same rule.
      </p>
      {error && (
        <p className="error" role="alert">
          {error}
        </p>
      )}
      {message && (
        <p className="fine" role="status">
          {message}{" "}
          {undoId && (
            <button type="button" className="inline-link" onClick={() => void undoAnswer()}>
              Undo
            </button>
          )}
        </p>
      )}
      {list.error && (
        <p className="error" role="alert">
          {list.error}
        </p>
      )}
      {list.loading && !list.data && <p className="fine">Loading uncategorized rows…</p>}
      {!list.loading && (list.data?.groups.length ?? 0) === 0 && (
        <p className="fine">Nothing is uncategorized.</p>
      )}
      {(list.data?.groups.length ?? 0) > 0 && (
        <ul className="act-uncat-list">
          {(expanded
            ? (list.data?.groups ?? [])
            : (list.data?.groups ?? []).slice(0, UNCATEGORIZED_PREVIEW)
          ).map((group) => (
            <li key={group.key} className="act-uncat-row">
              <div className="act-uncat-main">
                <strong>{group.key.toUpperCase()}</strong>
                <span className="fine">
                  {group.kind === "person"
                    ? `${formatPaise(group.total_paise)} across ${group.count} ${group.count === 1 ? "payment" : "payments"}`
                    : `${group.count} ${group.count === 1 ? "row" : "rows"} · ${formatPaise(group.total_paise)}`}
                </span>
                {group.hint?.reason && <span className="fine">Guess: {group.hint.reason}</span>}
                <span className="fine statement-text" title={group.narration}>
                  {group.narration}
                </span>
              </div>
              <div className="act-uncat-actions">
                <select
                  aria-label={`Category for ${group.key}`}
                  value={choice[group.key] ?? ""}
                  onChange={(event) => {
                    const value = event.target.value;
                    setChoice((current) => ({ ...current, [group.key]: value }));
                  }}
                >
                  <option value="">Choose a category</option>
                  {activeCategories.map((category) => (
                    <option key={category.id} value={category.id}>
                      {category.name}
                    </option>
                  ))}
                </select>
                <button
                  type="button"
                  className="ghost small"
                  disabled={!choice[group.key] || busy === group.key}
                  onClick={() => void answer(group)}
                >
                  {busy === group.key ? <Busy /> : "Answer"}
                </button>
              </div>
            </li>
          ))}
        </ul>
      )}
      {(list.data?.groups.length ?? 0) > UNCATEGORIZED_PREVIEW && (
        <button
          type="button"
          className="ghost small act-uncat-more"
          aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}
        >
          {expanded ? "Show fewer" : `Show all ${list.data?.groups.length} payees`}
        </button>
      )}
    </section>
  );
}

export function LedgerPanel() {
  const [draft, setDraft] = useState<Filters>(EMPTY);
  const [request, setRequest] = useState<Request>({
    filters: EMPTY,
    cursors: [null],
    index: 0,
    version: 0,
  });
  const [loaded, setLoaded] = useState<Loaded | null>(null);
  const [settledKey, setSettledKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [search, setSearch] = useState("");
  const [openMovement, setOpenMovement] = useState<Movement | null>(null);
  const [detail, setDetail] = useState<MovementDetail | null>(null);
  const [detailError, setDetailError] = useState<string | null>(null);
  const [detailLoading, setDetailLoading] = useState(false);
  const accounts = useApi<AccountSummary>("/api/accounts");

  const cursor = request.cursors[request.index] ?? null;
  const requestKey = `${JSON.stringify(request.filters)}|${cursor}|${request.version}`;
  const loading = settledKey !== requestKey;
  const rangeError =
    draft.start && draft.end && draft.start > draft.end
      ? "The start date is after the end date."
      : null;

  useEffect(() => {
    let alive = true;
    api<MovementList>(`/api/movements?${buildParams(request.filters, cursor)}`)
      .then((result) => {
        if (!alive) return;
        setLoaded({
          items: result.items,
          nextCursor: result.next_cursor,
          index: request.index,
        });
        setError(null);
      })
      .catch((caught: unknown) => {
        if (alive) setError(errorMessage(caught));
      })
      .finally(() => {
        if (alive) setSettledKey(requestKey);
      });
    return () => {
      alive = false;
    };
  }, [request, cursor, requestKey]);

  useEffect(() => {
    if (!openMovement) return;
    let alive = true;
    api<MovementDetail>(`/api/movements/${openMovement.id}`)
      .then((value) => {
        if (alive) setDetail(value);
      })
      .catch((caught: unknown) => {
        if (alive) setDetailError(errorMessage(caught));
      })
      .finally(() => {
        if (alive) setDetailLoading(false);
      });
    return () => {
      alive = false;
    };
  }, [openMovement]);

  function openDetails(movement: Movement) {
    setDetail(null);
    setDetailError(null);
    setDetailLoading(true);
    setOpenMovement(movement);
  }

  function reload(filters: Filters) {
    setRequest((current) => ({
      filters,
      cursors: [null],
      index: 0,
      version: current.version + 1,
    }));
  }

  function retry() {
    setRequest((current) => ({ ...current, version: current.version + 1 }));
  }

  function nextPage() {
    if (!loaded?.nextCursor || loading) return;
    const nextCursor = loaded.nextCursor;
    setRequest((current) => ({
      ...current,
      cursors: [...current.cursors.slice(0, loaded.index + 1), nextCursor],
      index: loaded.index + 1,
      version: current.version + 1,
    }));
  }

  function previousPage() {
    if (!loaded || loaded.index === 0 || loading) return;
    setRequest((current) => ({
      ...current,
      index: loaded.index - 1,
      version: current.version + 1,
    }));
  }

  const query = search.trim().toLowerCase();
  const visible = loaded
    ? query
      ? loaded.items.filter((movement) => matchesSearch(movement, query))
      : loaded.items
    : [];
  const chatSources = (detail?.sources ?? []).filter((source) => source.conversation_id);

  return (
    <section className="panel act-ledger">
      <header className="panel-head">
        <div>
          <h2>Movements</h2>
          <p className="muted">
            Every real money movement, newest first. Corrections are reversals; nothing is edited
            in place. Open a row for its evidence and the transfers that balanced it.
          </p>
        </div>
        <button
          type="button"
          className="ghost"
          onClick={retry}
          disabled={loading}
          aria-busy={loading}
        >
          {loading ? (
            <>
              <Busy />
              Refreshing…
            </>
          ) : (
            <>
              <RefreshCw size={14} strokeWidth={1.8} aria-hidden="true" />
              Refresh
            </>
          )}
        </button>
      </header>

      <UncategorizedSection onAnswered={retry} version={request.version} />

      <form
        className="act-toolbar"
        aria-label="Ledger filters"
        onSubmit={(event) => {
          event.preventDefault();
          if (rangeError) return;
          reload({ ...draft });
        }}
      >
        <label className="act-field act-search">
          Search this page
          <input
            type="search"
            value={search}
            placeholder="Payee, note, or category"
            onChange={(event) => setSearch(event.target.value)}
          />
        </label>
        <label className="act-field">
          Account
          <select
            value={draft.accountId}
            onChange={(event) => setDraft({ ...draft, accountId: event.target.value })}
          >
            <option value="">All accounts</option>
            {(accounts.data?.accounts ?? []).map((account) => (
              <option key={account.id} value={account.id}>
                {account.name}
              </option>
            ))}
          </select>
        </label>
        <label className="act-field">
          Kind
          <select
            value={draft.kind}
            onChange={(event) => setDraft({ ...draft, kind: event.target.value })}
          >
            <option value="">All kinds</option>
            {KINDS.map((option) => (
              <option key={option} value={option}>
                {humanizeKind(option)}
              </option>
            ))}
          </select>
        </label>
        <label className="act-field">
          From
          <input
            type="date"
            value={draft.start}
            onChange={(event) => setDraft({ ...draft, start: event.target.value })}
          />
        </label>
        <label className="act-field">
          To
          <input
            type="date"
            value={draft.end}
            onChange={(event) => setDraft({ ...draft, end: event.target.value })}
          />
        </label>
        <div className="act-toolbar-actions">
          <button
            type="button"
            className={draft.includeReversed ? "act-toggle on" : "act-toggle"}
            aria-pressed={draft.includeReversed}
            aria-label="Show reversed movements"
            title="Show reversed movements"
            onClick={() => setDraft({ ...draft, includeReversed: !draft.includeReversed })}
          >
            <Undo2 size={17} strokeWidth={2} aria-hidden="true" />
          </button>
          <button type="submit" disabled={loading || Boolean(rangeError)}>
            Apply filters
          </button>
          <button
            type="button"
            className="ghost"
            onClick={() => {
              setDraft(EMPTY);
              setSearch("");
              reload(EMPTY);
            }}
          >
            Clear
          </button>
        </div>
        {rangeError && (
          <p className="act-field-error" role="alert">
            {rangeError}
          </p>
        )}
        {accounts.error && (
          <p className="act-field-error" role="alert">
            Accounts didn’t load, so only the other filters apply.{" "}
            <button type="button" className="inline-link" onClick={accounts.reload}>
              Try again
            </button>
          </p>
        )}
      </form>

      <div className="act-status" aria-live="polite">
        <span>Page {loaded ? loaded.index + 1 : 1} · newest first · {PAGE_SIZE} per page</span>
        {query && loaded && (
          <span>
            {visible.length} of {loaded.items.length} on this page match
          </span>
        )}
        {loading && (
          <span className="act-busy">
            <Busy />
            Updating…
          </span>
        )}
      </div>

      {error && (
        <div className="act-error" role="alert">
          <AlertCircle size={18} strokeWidth={1.8} aria-hidden="true" />
          <div>
            <strong>The ledger couldn’t load.</strong>
            <p className="fine">{error}</p>
          </div>
          <button type="button" className="ghost small" onClick={retry}>
            Retry
          </button>
        </div>
      )}

      {!loaded && loading && <LedgerSkeleton />}

      {loaded && loaded.items.length === 0 && (
        <div className="act-empty">
          <p>
            {loaded.index > 0
              ? "No movements on this page."
              : "No movements match these filters."}
          </p>
          {loaded.index > 0 ? (
            <button type="button" className="ghost small" onClick={previousPage}>
              <ChevronLeft size={14} strokeWidth={1.8} aria-hidden="true" />
              Previous page
            </button>
          ) : (
            <button
              type="button"
              className="ghost small"
              onClick={() => {
                setDraft(EMPTY);
                setSearch("");
                reload(EMPTY);
              }}
            >
              Clear filters
            </button>
          )}
        </div>
      )}

      {loaded && loaded.items.length > 0 && visible.length === 0 && (
        <div className="act-empty">
          <p>No rows on this page match “{search.trim()}”.</p>
          <button type="button" className="ghost small" onClick={() => setSearch("")}>
            Clear search
          </button>
        </div>
      )}

      {visible.length > 0 && (
        <div className="act-list">
          <div className="act-list-head" aria-hidden="true">
            <span>Date</span>
            <span>Movement</span>
            <span>Category</span>
            <span>Amount</span>
            <span />
          </div>
          {visible.map((movement) => (
            <MovementRow key={movement.id} movement={movement} onOpen={openDetails} />
          ))}
        </div>
      )}

      {loaded && loaded.items.length > 0 && (
        <nav className="act-pager" aria-label="Ledger pages">
          <span className="act-pager-info">
            Page {loaded.index + 1}
            {loaded.nextCursor ? "" : " · end of the list"}
          </span>
          <div className="act-pager-buttons">
            <button
              type="button"
              className="ghost small"
              disabled={loaded.index === 0 || loading}
              onClick={previousPage}
            >
              <ChevronLeft size={14} strokeWidth={1.9} aria-hidden="true" />
              Previous
            </button>
            <button
              type="button"
              className="ghost small"
              disabled={!loaded.nextCursor || loading}
              onClick={nextPage}
            >
              Next
              <ChevronRight size={14} strokeWidth={1.9} aria-hidden="true" />
            </button>
          </div>
        </nav>
      )}

      {openMovement && (
        <Dialog.Root open onOpenChange={(open) => !open && setOpenMovement(null)}>
          <Dialog.Portal>
            <Dialog.Overlay className="dialog-overlay" />
            <Dialog.Content
              className="movement-dialog act-dialog"
              aria-describedby={undefined}
            >
              <div className="queue-head">
                <Dialog.Title>{movementLabel(openMovement)}</Dialog.Title>
                <Dialog.Close asChild>
                  <button type="button" className="ghost small icon-only" aria-label="Close details" title="Close details">
                    <X size={15} strokeWidth={1.8} aria-hidden="true" />
                  </button>
                </Dialog.Close>
              </div>
              <p className="movement-hero-amount">{formatPaise(openMovement.amount_paise)}</p>
              <dl className="detail-grid">
                <dt>ID</dt>
                <dd>{openMovement.id}</dd>
                <dt>Date</dt>
                <dd>{formatDate(openMovement.date)}</dd>
                <dt>Kind</dt>
                <dd>{humanizeKind(openMovement.kind)}</dd>
                <dt>Category</dt>
                <dd>{openMovement.category_name ?? "unlabelled"}</dd>
                <dt>Payee</dt>
                <dd>{openMovement.payee_name ?? "not set"}</dd>
                <dt>Statement text</dt>
                <dd className="statement-text">{openMovement.description}</dd>
                <dt>Amount</dt>
                <dd>{formatPaise(openMovement.amount_paise)}</dd>
                <dt>State</dt>
                <dd>{openMovement.reversed ? "reversed" : "live"}</dd>
                <dt>Evidence</dt>
                <dd>
                  {openMovement.evidence.length
                    ? openMovement.evidence
                        .map((item) => item.replaceAll("_", " "))
                        .join(", ")
                    : "none"}
                </dd>
              </dl>
              <h3 className="section-title">Recorded from</h3>
              {detailLoading && <p className="fine">Opening the source row…</p>}
              {detailError && (
                <p className="error" role="alert">
                  {detailError}
                </p>
              )}
              {!detailLoading && !detailError && chatSources.length === 0 && (
                <p className="fine">No originating chat message is recorded for this movement.</p>
              )}
              {chatSources.length > 0 && (
                <ul className="transfers">
                  {chatSources.map((source) => {
                    const thread = source.conversation_id;
                    if (!thread) return null;
                    return (
                      <li key={source.message_id ?? source.ref ?? thread}>
                        <span>Chat message</span>
                        <button
                          type="button"
                          className="inline-link"
                          onClick={() => {
                            openConversation(thread, source.message_id);
                            setOpenMovement(null);
                          }}
                        >
                          {source.message_id ? "Open message" : "Open conversation"}
                        </button>
                      </li>
                    );
                  })}
                </ul>
              )}
              <h3 className="section-title">Transfers</h3>
              {openMovement.transfers.length > 0 ? (
                <ul className="transfers">
                  {openMovement.transfers.map((transfer) => (
                    <li key={transfer.transfer_id}>
                      <span>
                        {transfer.from_name} → {transfer.to_name}
                      </span>
                      <span>{formatPaise(transfer.amount_paise)}</span>
                    </li>
                  ))}
                </ul>
              ) : (
                <p className="fine">No transfer legs; this row does not move money between accounts.</p>
              )}
              <div className="button-row">
                <button
                  type="button"
                  className="ask-link"
                  onClick={() => {
                    askAgent(
                      `Explain the movement "${movementLabel(openMovement)}" for ${formatPaise(openMovement.amount_paise)} on ${formatDate(openMovement.date)}.`,
                    );
                    setOpenMovement(null);
                  }}
                >
                  <MessageCircle size={13} aria-hidden="true" />
                  Ask about this
                </button>
              </div>
            </Dialog.Content>
          </Dialog.Portal>
        </Dialog.Root>
      )}
    </section>
  );
}
