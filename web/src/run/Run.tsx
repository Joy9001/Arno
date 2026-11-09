import { useState } from "react";
import { ArrowLeft, ChevronDown, ExternalLink, X } from "lucide-react";
import { askAgent } from "../ask";
import { displayText } from "../chat/display-text";
import { Orb } from "../Orb";
import type { ImportTask, RunStage, RunStep } from "../types";
import { CONTINUE_PROMPT, WHY_PROMPT, clock, elapsed, runEnding, stepTime, useRun } from "./runData";
import "./run.css";

function StepRow({
  step,
  selected,
  onSelect,
}: {
  step: RunStep;
  selected?: boolean;
  onSelect?: () => void;
}) {
  const [open, setOpen] = useState(false);
  const icon =
    step.status === "running" ? (
      <Orb size={18} live thinking />
    ) : step.status === "ok" ? (
      "✓"
    ) : (
      "✕"
    );
  return (
    <li
      className={`run-step ${step.status}${selected ? " selected" : ""}`}
      onClick={onSelect}
      onKeyDown={(event) => {
        if (onSelect && (event.key === "Enter" || event.key === " ")) {
          event.preventDefault();
          onSelect();
        }
      }}
      tabIndex={onSelect ? 0 : undefined}
      aria-current={selected ? "step" : undefined}
    >
      <span className="run-step-icon" aria-hidden="true">
        {icon}
      </span>
      <span className="run-step-title">
        {step.title}
        <span className="sr-only">
          {step.status === "failed" ? ", failed" : step.status === "cut_off" ? ", cut off" : ""}
        </span>
      </span>
      <span className="run-step-time">{step.status === "cut_off" ? "—" : stepTime(step.seconds)}</span>
      {step.calls.length > 0 && (
        <span className="run-calls">
          {step.calls.map((call) => (
            <span key={call.name} className={call.failed ? "run-fn failed" : "run-fn"}>
              {call.name}
              {call.count > 1 && <b>×{call.count}</b>}
              {call.failed && <b>failed</b>}
            </span>
          ))}
        </span>
      )}
      {step.error && <span className="run-step-error">{displayText(step.error)}</span>}
      {step.status === "cut_off" && (
        <span className="run-step-error">Cut off: the run stopped before this step finished.</span>
      )}
      {step.purpose && (
        <span className="run-note">
          <em>Agent’s note</em>
          {displayText(step.purpose)}
        </span>
      )}
      {step.reasoning && !onSelect && (
        <span className="run-reasoning">
          <button
            type="button"
            className="run-fold"
            aria-expanded={open}
            onClick={(event) => {
              event.stopPropagation();
              setOpen((value) => !value);
            }}
          >
            <ChevronDown size={13} aria-hidden="true" />
            Reasoning
          </button>
          {open && <span className="run-reasoning-text">{displayText(step.reasoning)}</span>}
        </span>
      )}
    </li>
  );
}

function StageNode({ stage, index }: { stage: RunStage; index: number }) {
  if (stage.status === "running") return <Orb size={28} live thinking />;
  if (stage.status === "done") return <>✓</>;
  if (stage.status === "stopped") return <>!</>;
  return <>{index + 1}</>;
}

const VISIBLE_STEPS = 4;

function Stage({ stage, index, steps, selected, onSelect }: {
  stage: RunStage;
  index: number;
  steps: RunStep[];
  selected?: string | null;
  onSelect?: (id: string) => void;
}) {
  const [expanded, setExpanded] = useState(false);
  const selectedIndex = steps.findIndex((step) => step.id === selected);
  const start = Math.min(Math.max(0, steps.length - VISIBLE_STEPS), selectedIndex < 0 ? steps.length : selectedIndex);
  const visible = expanded ? steps : steps.slice(start, start + VISIBLE_STEPS);
  return (
    <li className={`run-stage ${stage.status}`}>
      <span className="run-node" aria-hidden="true"><StageNode stage={stage} index={index} /></span>
      <div className="run-stage-head">
        <b>{stage.title}</b>
        <span>
          {stage.status === "not_reached"
            ? "Not reached"
            : stage.steps.length
              ? `${stage.steps.length} ${stage.steps.length === 1 ? "step" : "steps"} · ${clock(stage.seconds)}`
              : ""}
        </span>
      </div>
      {visible.length > 0 && (
        <ol className="run-steps">
          {visible.map((step, stepIndex) => (
            <StepWithDivider key={step.id} step={step} prev={steps[(expanded ? 0 : start) + stepIndex - 1]}
              selected={selected === step.id} onSelect={onSelect ? () => onSelect(step.id) : undefined} />
          ))}
        </ol>
      )}
      {steps.length > VISIBLE_STEPS && (
        <button type="button" className="run-view-all" aria-expanded={expanded}
          onClick={() => setExpanded((value) => !value)}>
          {expanded ? "Show fewer" : `View all ${steps.length} steps`}
        </button>
      )}
    </li>
  );
}

export function RunBody({
  task,
  selected,
  onSelect,
}: {
  task: ImportTask;
  selected?: string | null;
  onSelect?: (id: string) => void;
}) {
  const ending = runEnding(task);
  const byId = new Map(task.run.steps.map((step) => [step.id, step]));
  const pct = task.run.limit ? Math.min(100, (task.run.used / task.run.limit) * 100) : 0;
  const meterTone = task.run.stop === "budget" ? "over" : pct >= 75 ? "warn" : "";
  const segments = task.run.segments.length;
  return (
    <>
      {ending.tone !== "live" && ending.tone !== "done" && (
        <section className={`run-ending ${ending.tone}`} aria-live="polite">
          <h3>{ending.title}</h3>
          <p>{displayText(ending.body)}</p>
          <div className="run-actions">
            {ending.tone === "paused" && (
              <button
                type="button"
                className="run-primary"
                disabled={task.follow_up === "replying"}
                onClick={() => askAgent(CONTINUE_PROMPT, { send: true })}
              >
                Continue · {task.run.limit} more steps
              </button>
            )}
            <button
              type="button"
              className="ask-link"
              disabled={task.follow_up === "replying"}
              onClick={() => askAgent(WHY_PROMPT)}
            >
              Ask why
            </button>
          </div>
        </section>
      )}

      <div className={`run-budget ${meterTone}`}>
        <div className="run-budget-row">
          <span>Step budget{segments > 1 ? ` · run ${segments}` : ""}</span>
          <span>
            <b>{task.run.used}</b> of {task.run.limit} used
          </span>
        </div>
        <div
          className="run-budget-track"
          role="meter"
          aria-label="Steps used"
          aria-valuemin={0}
          aria-valuemax={task.run.limit}
          aria-valuenow={task.run.used}
        >
          <i style={{ width: `${pct}%` }} />
        </div>
      </div>

      {ending.tone === "live" && (
        <div className="run-now" aria-live="polite">
          <Orb size={22} live thinking />
          <span>{ending.line.replace(/^Step \d+ of \d+ · /, "")}</span>
        </div>
      )}

      <ol className="run-spine">
        {task.run.stages.map((stage, index) => (
          <Stage key={stage.key} stage={stage} index={index}
            steps={stage.steps.map((id) => byId.get(id)).filter((step): step is RunStep => step !== undefined)}
            selected={selected} onSelect={onSelect} />
        ))}
      </ol>
    </>
  );
}

function StepWithDivider({
  step,
  prev,
  selected,
  onSelect,
}: {
  step: RunStep;
  prev?: RunStep;
  selected: boolean;
  onSelect?: () => void;
}) {
  return (
    <>
      {prev && prev.segment !== step.segment && (
        <li className="run-continued" aria-label="Continued">
          Continued · a fresh step budget
        </li>
      )}
      <StepRow step={step} selected={selected} onSelect={onSelect} />
    </>
  );
}

function RunHead({ task, compact = false }: { task: ImportTask; compact?: boolean }) {
  const ending = runEnding(task);
  const file = task.imports[0]?.filename;
  return (
    <div className={compact ? "run-head compact" : "run-head"}>
      <h2>Reading your statement</h2>
      <p className="run-meta">
        <span className={`run-state ${ending.tone}`}>
          {ending.tone === "live" ? "Running" : ending.line} · {clock(elapsed(task))}
        </span>
        {file && <span>{displayText(file)}</span>}
        {task.model && <span className="run-model">{task.model}</span>}
      </p>
    </div>
  );
}

/** Direction A: the run on the canvas beside the conversation. */
export function RunPanel({ taskId, onClose }: { taskId: string; onClose: () => void }) {
  const { task, error, reload } = useRun(taskId);
  return (
    <div className="run-panel">
      <header className="evidence-head">
        {task ? <RunHead task={task} compact /> : <h2>Reading your statement</h2>}
        <div className="run-head-actions">
          <a className="pill-button" href={`#activity/run/${encodeURIComponent(taskId)}`}>
            <ExternalLink size={14} aria-hidden="true" />
            <span className="run-open-label">Open full page</span>
            <span className="sr-only">Open the run as a full page</span>
          </a>
          <button type="button" className="run-close" aria-label="Close run" onClick={onClose}>
            <X size={16} aria-hidden="true" />
          </button>
        </div>
      </header>
      <div className="run-scroll">
        {task ? (
          <RunBody task={task} />
        ) : error ? (
          <p className="error" role="alert">
            {error}{" "}
            <button type="button" className="pill-button" onClick={reload}>
              Retry
            </button>
          </p>
        ) : (
          <p className="fine">Loading the run…</p>
        )}
      </div>
    </div>
  );
}

function StepDetail({ step, index, total }: { step: RunStep; index: number; total: number }) {
  const lines = (step.code ?? "").split("\n");
  const shown = lines.slice(0, 14).join("\n");
  return (
    <section className="run-detail" aria-label="Step detail">
      <h3>{step.title}</h3>
      <p className="run-meta">
        <span>
          Step {index + 1} of {total}
        </span>
        <span className={`run-state ${step.status === "ok" ? "done" : step.status === "running" ? "live" : "failed"}`}>
          {step.status === "ok" ? "Done" : step.status === "running" ? "Running" : step.status === "cut_off" ? "Cut off" : "Failed"}
        </span>
        <span>{new Date(step.started_at).toLocaleTimeString("en-IN", { hour: "numeric", minute: "2-digit", second: "2-digit" })}</span>
        <span>{stepTime(step.seconds)}</span>
      </p>
      {step.calls.length > 0 && (
        <p className="run-calls">
          {step.calls.map((call) => (
            <span key={call.name} className={call.failed ? "run-fn failed" : "run-fn"}>
              {call.name}
              {call.count > 1 && <b>×{call.count}</b>}
            </span>
          ))}
        </p>
      )}
      {step.error && (
        <>
          <h4>What went wrong</h4>
          <pre className="run-result">{displayText(step.error)}</pre>
        </>
      )}
      {step.purpose && (
        <>
          <h4>
            Agent’s note <span className="run-tag">its own words</span>
          </h4>
          <p className="run-quote">{displayText(step.purpose)}</p>
        </>
      )}
      {step.code && (
        <>
          <h4>
            Script <span className="run-tag">{lines.length > 14 ? "first 14 lines" : "in full"}</span>
          </h4>
          <pre className="run-code">{shown}</pre>
        </>
      )}
      {step.result && (
        <>
          <h4>
            Result <span className="run-tag">what the script printed or returned</span>
          </h4>
          <pre className="run-output">{displayText(step.result)}</pre>
        </>
      )}
      {step.reasoning && (
        <details className="run-thinking">
          <summary>
            Reasoning <span className="run-tag">its own words</span>
          </summary>
          <p>{displayText(step.reasoning)}</p>
        </details>
      )}
    </section>
  );
}

/** Direction B: the run as a page, with one step in full. */
export function RunPage({ taskId }: { taskId: string }) {
  const { task, error, reload } = useRun(taskId);
  const [selected, setSelected] = useState<string | null>(null);
  const steps = task?.run.steps ?? [];
  const fallback =
    [...steps].reverse().find((step) => step.status === "failed" || step.status === "running") ??
    steps.at(-1);
  const current = steps.find((step) => step.id === selected) ?? fallback;
  const index = current ? steps.indexOf(current) : -1;
  return (
    <section className="run-page" aria-label="Parser run">
      <div className="area-head">
        <h2>
          <a className="run-back" href="#activity/imports" aria-label="Back to imports">
            <ArrowLeft size={18} aria-hidden="true" />
          </a>
          Run
        </h2>
        <span className="run-crumb">Activity / Imports / {task?.imports[0]?.filename ? displayText(task.imports[0].filename) : "Statement"}</span>
      </div>
      {task ? (
        <div className="run-page-cols">
          <div className="run-page-main">
            <RunHead task={task} />
            <RunBody task={task} selected={current?.id ?? null} onSelect={setSelected} />
          </div>
          {current && (
            <div className="run-page-detail">
              <StepDetail step={current} index={index} total={steps.length} />
              <div className="run-pager">
                <button type="button" className="pill-button" disabled={index <= 0} onClick={() => setSelected(steps[index - 1]!.id)}>
                  Previous step
                </button>
                <button type="button" className="pill-button" disabled={index >= steps.length - 1} onClick={() => setSelected(steps[index + 1]!.id)}>
                  Next step
                </button>
              </div>
            </div>
          )}
        </div>
      ) : error ? (
        <p className="error" role="alert">
          {error}{" "}
          <button type="button" className="pill-button" onClick={reload}>
            Retry
          </button>
        </p>
      ) : (
        <p className="fine run-loading">Loading the run…</p>
      )}
    </section>
  );
}
