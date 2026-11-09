import { useRef, useState, type DragEvent } from "react";
import "./first-run.css";
import { Check, FileDown, Lock, MessageCircleQuestion, Upload, Wallet } from "lucide-react";
import { askAgent, attachAgent } from "../ask";
import { Orb } from "../Orb";
import { BALANCE_PROMPT, firstRunSteps, type Step } from "./firstRunSteps";
import type { HomePayload } from "./AskHome";
import { STATEMENT_ACCEPT } from "./statement-attachment";

/** Starter prompts that work before the ledger holds anything. */
const FIRST_STARTERS = [
  { Icon: MessageCircleQuestion, text: "What can you do?" },
  { Icon: FileDown, text: "How do I download a statement?" },
  { Icon: Wallet, text: BALANCE_PROMPT, label: "My savings balance is…" },
] as const;

export function FirstRunStarters() {
  return (
    <div className="aui-starters">
      {FIRST_STARTERS.map(({ Icon, text, ...rest }) => (
        <button key={text} type="button" className="aui-starter" onClick={() => askAgent(text)}>
          <Icon size={16} strokeWidth={1.8} aria-hidden="true" />
          <span className="aui-starter-copy">
            <span className="aui-starter-title">{"label" in rest ? rest.label : text}</span>
          </span>
        </button>
      ))}
    </div>
  );
}

function StatementDrop() {
  const input = useRef<HTMLInputElement>(null);
  const [over, setOver] = useState(false);

  function take(files: FileList | null) {
    const list = Array.from(files ?? []);
    if (list.length) attachAgent(list);
  }

  function drop(event: DragEvent<HTMLDivElement>) {
    event.preventDefault();
    setOver(false);
    take(event.dataTransfer.files);
  }

  return (
    <div
      className={over ? "first-drop over" : "first-drop"}
      onDragOver={(event) => {
        event.preventDefault();
        setOver(true);
      }}
      onDragLeave={() => setOver(false)}
      onDrop={drop}
    >
      <span className="first-drop-icon" aria-hidden="true">
        <Upload size={24} />
      </span>
      <h3>Drop a bank statement</h3>
      <p>
        A PDF or CSV from your netbanking. I read every page, you check every row, and nothing
        reaches your ledger until you commit it.
      </p>
      <div className="first-drop-actions">
        <button type="button" className="pill-button lime" onClick={() => input.current?.click()}>
          Choose a file
        </button>
        <button type="button" className="pill-button" onClick={() => askAgent(BALANCE_PROMPT)}>
          Tell me a balance instead
        </button>
      </div>
      <p className="first-drop-fine">
        <Lock size={14} aria-hidden="true" />A locked PDF asks for its password on the import card,
        never in chat.
      </p>
      <input
        ref={input}
        type="file"
        accept={STATEMENT_ACCEPT}
        multiple
        hidden
        onChange={(event) => {
          take(event.currentTarget.files);
          event.currentTarget.value = "";
        }}
      />
    </div>
  );
}

function Preview({ kind }: { kind: Step["preview"] }) {
  if (kind === "curve") {
    return (
      <span className="first-preview" aria-hidden="true">
        <svg viewBox="0 0 92 58" preserveAspectRatio="none">
          <path d="M0 40 C14 38 20 22 34 26 S56 44 68 30 S84 18 92 20 L92 58 L0 58Z" />
          <path d="M0 40 C14 38 20 22 34 26 S56 44 68 30 S84 18 92 20" />
        </svg>
      </span>
    );
  }
  return <span className={`first-preview first-preview-${kind}`} aria-hidden="true" />;
}

/**
 * The agent's home before its usual cards have anything to say. An empty ledger leads with the
 * statement drop; after that, the list shows what is ready and what each missing piece needs.
 */
export function FirstRun({ data }: { data: HomePayload }) {
  const steps = firstRunSteps(data);
  const ready = steps.filter((step) => step.ready).length;
  const empty = data.ledger.movements === 0;
  return (
    <section className={empty ? "first-run empty" : "first-run"} aria-label="Get started">
      <p className="first-run-say">
        <Orb size={18} />
        <span>
          {empty ? (
            <>
              <b>I don’t know your money yet.</b> One statement is enough to start.
            </>
          ) : (
            <b>Almost there.</b>
          )}
        </span>
      </p>
      <div className="first-run-grid">
        {empty && <StatementDrop />}
        <div className="first-unlocks">
          <div className="first-unlocks-head">
            <h3>What it unlocks</h3>
            <span>
              {ready} of {steps.length} ready
            </span>
          </div>
          <ul>
            {steps.map((step) => (
              <li key={step.key} className={step.ready ? "ready" : undefined}>
                <span className="first-mark" aria-hidden="true">
                  {step.ready && <Check size={14} strokeWidth={3} />}
                </span>
                <span className="first-copy">
                  <b>
                    {step.title}
                    <span className="sr-only">{step.ready ? " (ready)" : " (not ready yet)"}</span>
                  </b>
                  {step.figure && <strong className="first-figure">{step.figure}</strong>}
                  <span>{step.detail}</span>
                </span>
                {step.action ? (
                  <button
                    type="button"
                    className={step.action.primary ? "pill-button lime" : "pill-button"}
                    onClick={step.action.run}
                  >
                    {step.action.label}
                  </button>
                ) : (
                  <Preview kind={step.preview} />
                )}
              </li>
            ))}
          </ul>
        </div>
      </div>
    </section>
  );
}
