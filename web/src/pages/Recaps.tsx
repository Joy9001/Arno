import { useEffect, useState, type KeyboardEvent } from "react";
import { ChevronLeft, ChevronRight, Upload } from "lucide-react";
import { api } from "../api";
import { askAgent } from "../ask";
import { displayText } from "../chat/display-text";
import { formatDate } from "../format";
import { Loading } from "../Loading";
import { goTo } from "../nav";
import { Orb } from "../Orb";
import { useApi } from "../useApi";
import type { RecapDetail, RecapList, RecapPeriod, RecapStory } from "../types";
import { RecapRows } from "./RecapRows";
import "../chat/first-run.css";

const KINDS: RecapPeriod["kind"][] = ["day", "week", "month"];
const KIND_LABEL: Record<RecapPeriod["kind"], string> = {
  day: "Daily",
  week: "Weekly",
  month: "Monthly",
};
const TONE: Record<RecapPeriod["kind"], string> = {
  day: "cobalt",
  week: "sun",
  month: "mesh",
};

/** The open recap: the hash's id when it is listed, else the newest unopened one. */
function initialPeriod(periods: RecapPeriod[]): RecapPeriod {
  const prefix = "#recaps/";
  const hash = window.location.hash;
  const fromHash = hash.startsWith(prefix) ? hash.slice(prefix.length) : "";
  return (
    periods.find((entry) => entry.id === fromHash) ??
    periods.find((entry) => entry.new) ??
    periods.find((entry) => entry.kind === "month") ??
    periods[0]
  );
}

/** Before the ledger has any spending there is no story to tell; this says how one starts. */
function RecapsEmpty() {
  return (
    <section className="recaps">
      <aside className="recap-list" aria-label="Recaps">
        <h2>Recaps</h2>
        <p className="fine">Built from your ledger rows. Every figure is a sum you can open.</p>
        <p className="recap-none">
          <b>No recaps yet</b>
          Daily, weekly, and monthly recaps appear here once the ledger has spending.
        </p>
      </aside>
      <div className="recap-stage recap-first">
        <div className="recap-frame" aria-hidden="true">
          <div className="story-progress">
            <i />
            <i />
            <i />
            <i />
          </div>
          <div className="recap-frame-head">
            <Orb size={26} />
            Your first recap
          </div>
          <span className="recap-sk" style={{ top: 112, width: 110, height: 16 }} />
          <span className="recap-sk" style={{ top: 144, width: 230, height: 84, borderRadius: 16 }} />
          <span className="recap-sk" style={{ top: 250, width: 270, height: 18 }} />
          <span className="recap-sk" style={{ top: 276, width: 200, height: 18 }} />
          <div className="recap-frame-bars">
            <i style={{ width: "84%" }} />
            <i style={{ width: "58%" }} />
            <i style={{ width: "36%" }} />
            <i style={{ width: "70%" }} />
          </div>
          <span className="recap-frame-ask" />
        </div>
        <div className="recap-first-copy">
          <h3>Your first recap starts with a statement</h3>
          <p>
            Import one and its days, weeks, and months become recaps you can play. Every figure in
            them opens to the rows it was added up from.
          </p>
          <ul>
            <li>What you spent, and how it compares</li>
            <li>Where it went, category by category</li>
            <li>The biggest and the most frequent spends</li>
          </ul>
          <div className="recap-first-actions">
            <button type="button" className="pill-button lime" onClick={() => goTo("activity", "imports")}>
              <Upload size={14} aria-hidden="true" />
              Import a statement
            </button>
            <button type="button" className="ask-link" onClick={() => askAgent("What will my recaps show?")}>
              Ask what recaps show
            </button>
          </div>
        </div>
      </div>
    </section>
  );
}

/** The headline figure, as the server named it; `recurring` counts charges instead of money. */
function bigText(story: RecapStory): string {
  if (story.key === "recurring") {
    const count = story.figure.count ?? 0;
    return `${count} ${count === 1 ? "charge" : "charges"}`;
  }
  if (story.key === "where" || story.key === "frequent" || story.key === "change") {
    return displayText(story.figure.name ?? "");
  }
  return displayText(story.figure.text ?? "");
}

export function RecapsPage() {
  const list = useApi<RecapList>("/api/recaps");
  if (!list.data && !list.error) {
    return (
      <section className="recaps">
        <Loading label="Reading the ledger…" />
      </section>
    );
  }
  if (list.error) {
    return (
      <section className="recaps">
        <aside className="recap-list" aria-label="Recaps">
          <h2>Recaps</h2>
        </aside>
        <div className="recap-stage">
          <p className="story-line">The recaps couldn&apos;t be read: {list.error}</p>
          <button type="button" className="pill-button" onClick={list.reload}>
            Retry
          </button>
        </div>
      </section>
    );
  }
  const periods = list.data?.periods ?? [];
  if (!periods.length || periods.every((entry) => !entry.spent || entry.spent.paise === 0)) {
    return <RecapsEmpty />;
  }
  return <RecapStories periods={periods} />;
}

function RecapStories({ periods: initial }: { periods: RecapPeriod[] }) {
  const [periods, setPeriods] = useState(initial);
  const [selected, setSelected] = useState(() => initialPeriod(initial).id);
  const [index, setIndex] = useState(0);
  const [sheet, setSheet] = useState<{
    reference: { story: string; key: string | null };
    title: string;
  } | null>(null);

  const period = periods.find((entry) => entry.id === selected) ?? periods[0];
  const detail = useApi<RecapDetail>(`/api/recaps/${period.id}`);
  const ready = detail.loading ? null : detail.data;
  const stories = detail.error ? [] : ready?.stories ?? [];
  const story = stories[Math.min(index, stories.length - 1)];

  // Opening a recap clears its New mark once; a failure leaves the pill for the next visit.
  useEffect(() => {
    if (!period.new) return;
    void api<{ period_id: string; seen: boolean }>(`/api/recaps/${period.id}/seen`, {
      method: "POST",
    })
      .then(() => {
        setPeriods((entries) =>
          entries.map((entry) => (entry.id === period.id ? { ...entry, new: false } : entry)),
        );
        window.dispatchEvent(new Event("arno:recaps-seen"));
      })
      .catch(() => {
        // The badge and the pill retry on the next open.
      });
  }, [period.id, period.new]);

  function choose(id: string) {
    setSelected(id);
    setIndex(0);
    setSheet(null);
    window.history.replaceState(null, "", "#recaps/" + id);
  }

  function openRows(reference: { story: string; key: string | null }, title: string) {
    setSheet({ reference, title });
  }

  function step(delta: number) {
    if (!stories.length) return;
    setIndex((value) => Math.min(stories.length - 1, Math.max(0, value + delta)));
  }

  function onKey(event: KeyboardEvent<HTMLDivElement>) {
    if (event.key === "ArrowRight") step(1);
    if (event.key === "ArrowLeft") step(-1);
  }

  const peak = Math.max(...(story?.bars ?? []).map((bar) => bar.amount.paise), 1);

  return (
    <section className="recaps">
      <aside className="recap-list" aria-label="Recaps">
        <h2>Recaps</h2>
        <p className="fine">Built from your ledger rows. Every figure is a sum you can open.</p>
        {KINDS.map((kind) => {
          const group = periods.filter((entry) => entry.kind === kind);
          if (!group.length) return null;
          return (
            <div key={kind} className="recap-group">
              <span className="recap-kind">{KIND_LABEL[kind]}</span>
              {group.map((entry) => (
                <button
                  key={entry.id}
                  type="button"
                  className={entry.id === selected ? "recap-item on" : "recap-item"}
                  aria-current={entry.id === selected ? "true" : undefined}
                  onClick={() => choose(entry.id)}
                >
                  <span className={`recap-thumb ${TONE[entry.kind]}`} aria-hidden="true" />
                  <span>
                    <b>
                      {displayText(entry.title)}
                      {entry.new && <em className="recap-new">New</em>}
                    </b>
                    <span>
                      {formatDate(entry.start)}
                      {entry.end !== entry.start ? ` – ${formatDate(entry.end)}` : ""}
                      {entry.spent ? ` · ${displayText(entry.spent.text)}` : ""}
                    </span>
                  </span>
                </button>
              ))}
            </div>
          );
        })}
      </aside>

      <nav className="recap-picker" aria-label="Recap periods">
        {periods.map((entry) => (
          <button
            key={entry.id}
            type="button"
            className={entry.id === selected ? "recap-pick on" : "recap-pick"}
            aria-current={entry.id === selected ? "true" : undefined}
            onClick={() => choose(entry.id)}
          >
            {displayText(entry.title)}
            {entry.new && <em className="recap-new">New</em>}
          </button>
        ))}
      </nav>

      <div className="recap-stage">
        <button type="button" className="recap-nav" aria-label="Previous story" disabled={index === 0} onClick={() => step(-1)}>
          <ChevronLeft size={22} aria-hidden="true" />
        </button>
        <div
          className={`story ${TONE[period.kind]}`}
          tabIndex={0}
          role="group"
          aria-roledescription="story"
          aria-label={`${displayText(period.title)}, story ${index + 1} of ${stories.length || 1}`}
          onKeyDown={onKey}
        >
          <div className="story-progress" aria-hidden="true">
            {(stories.length ? stories : [null]).map((_, position) => (
              <i key={position} className={position <= index ? "done" : ""} />
            ))}
          </div>
          <div className="story-head">
            <Orb size={28} />
            <b>Arno</b>
            <span>· {displayText(period.title)}</span>
          </div>
          {detail.loading ? (
            <div className="story-loading">
              <Loading label="Adding up the rows…" />
            </div>
          ) : detail.error ? (
            <>
              <p className="story-line">The recap couldn&apos;t be read: {detail.error}</p>
              <button type="button" className="pill-button" onClick={detail.reload}>
                Retry
              </button>
            </>
          ) : story ? (
            <>
              <span className="story-label">{displayText(story.label)}</span>
              <strong className={bigText(story).length > 9 ? "story-big long" : "story-big"}>
                {bigText(story)}
              </strong>
              <p className="story-line">{displayText(story.line)}</p>
              {story.key === "total" && ready && !ready.coverage.complete && (
                <p className="story-note">
                  No statement covers part of this period, so some spending may be missing.
                </p>
              )}
              {story.bars && (
                <div className="story-bars">
                  {story.bars.map((bar) => {
                    const content = (
                      <>
                        <span>{displayText(bar.name)}</span>
                        <i style={{ width: `${Math.max(4, (bar.amount.paise / peak) * 100)}%` }} />
                        <b>{displayText(bar.amount.text)}</b>
                      </>
                    );
                    const rows = bar.rows;
                    return rows ? (
                      <button
                        key={bar.name}
                        type="button"
                        className="story-bar story-bar-button"
                        onClick={() => openRows(rows, story.label)}
                      >
                        {content}
                      </button>
                    ) : (
                      <div key={bar.name} className="story-bar">
                        {content}
                      </div>
                    );
                  })}
                </div>
              )}
              {story.key === "recurring" && story.charges && (
                <div className="story-charges">
                  {story.charges.map((charge) => (
                    <div key={`${charge.merchant}-${charge.last_date}`} className="story-charge">
                      <span>{displayText(charge.merchant)}</span>
                      <b>
                        {displayText(charge.amount.text)} every {charge.every}
                      </b>
                      <button
                        type="button"
                        className="pill-button"
                        onClick={() =>
                          askAgent(
                            `Track ${displayText(charge.merchant)} as a recurring charge: ${charge.amount.text} every ${charge.every}.`,
                          )
                        }
                      >
                        Track it
                      </button>
                    </div>
                  ))}
                </div>
              )}
            </>
          ) : null}
          <div className="story-foot">
            {story?.rows ? (
              <button type="button" className="story-cite" onClick={() => openRows(story.rows!, story.label)}>
                Open the rows behind this
              </button>
            ) : (
              <div className="story-cite" />
            )}
            <button
              type="button"
              className="story-ask"
              onClick={() => askAgent(`Tell me my recap for ${period.title} (${period.id}). `)}
            >
              Ask about {displayText(period.title).toLowerCase()}…
            </button>
          </div>
        </div>
        <button
          type="button"
          className="recap-nav"
          aria-label="Next story"
          disabled={!stories.length || index >= stories.length - 1}
          onClick={() => step(1)}
        >
          <ChevronRight size={22} aria-hidden="true" />
        </button>
      </div>

      {sheet && (
        <RecapRows
          periodId={period.id}
          reference={sheet.reference}
          title={sheet.title}
          onClose={() => setSheet(null)}
        />
      )}
    </section>
  );
}
