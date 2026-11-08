import { useState } from "react";
import {
  defineToolkit,
  type ToolCallMessagePartComponent,
  type ToolCallMessagePartProps,
} from "@assistant-ui/react";
import { AgentPlan } from "@/components/assistant-ui/elements/agent-plan.aui";
import { ToolFallback } from "@/components/tool-fallback.aui";
import { ChatToolFallback } from "./parts";
import { DelegateTaskCard } from "./delegate-task-card";
import { displayText } from "./display-text";

type ToolCopy = {
  label: string;
  running: string;
  done: string;
};

const TOOL_COPY: Record<string, ToolCopy> = {
  run_code: {
    label: "Working through the numbers",
    running: "Running a read-only script…",
    done: "Finished the read-only script.",
  },
  get_financial_context: {
    label: "Reading your money",
    running: "Reading accounts, schedules, and balances…",
    done: "Read your accounts, schedules, and balances.",
  },
  query_movements: {
    label: "Listing movements",
    running: "Finding the matching movements…",
    done: "Found the matching movements.",
  },
  summarize_spending: {
    label: "Totalling spending",
    running: "Adding up spending…",
    done: "Totalled the spending.",
  },
  run_query: {
    label: "Running a read-only query",
    running: "Running a read-only query…",
    done: "Ran the read-only query.",
  },
  project: {
    label: "Projecting your cash",
    running: "Running the projection…",
    done: "Ran the projection.",
  },
  amortize: {
    label: "Working out a loan",
    running: "Working out the loan schedule…",
    done: "Worked out the loan schedule.",
  },
  compound: {
    label: "Working out growth",
    running: "Working out growth at the assumed rate…",
    done: "Worked out growth at the assumed rate.",
  },
  show_chart: {
    label: "Drawing a chart",
    running: "Drawing the chart…",
    done: "Drew the chart.",
  },
  show_table: {
    label: "Building a table",
    running: "Building the table…",
    done: "Built the table.",
  },
  present: {
    label: "Composing the answer",
    running: "Composing the answer…",
    done: "Composed the answer.",
  },
  record_movement: {
    label: "Logging a movement",
    running: "Logging the movement…",
    done: "Logged the movement.",
  },
  correct_movement: {
    label: "Fixing a movement",
    running: "Fixing the movement…",
    done: "Fixed the movement.",
  },
  update_setup: {
    label: "Updating setup",
    running: "Saving the setup change…",
    done: "Saved the setup change.",
  },
  preference: {
    label: "Saving a preference",
    running: "Saving the preference…",
    done: "Saved the preference.",
  },
  import_document: {
    label: "Starting a statement import",
    running: "Starting the import…",
    done: "Started the import.",
  },
  undo: {
    label: "Undoing a change",
    running: "Undoing the change…",
    done: "Undid the change.",
  },
};

function needsFallback(props: ToolCallMessagePartProps): boolean {
  return Boolean(props.approval) || props.status?.type === "requires-action";
}

function summary(copy: ToolCopy, props: ToolCallMessagePartProps): string {
  if (props.status?.type === "running") return copy.running;
  if (props.status?.type === "incomplete") {
    return props.status.reason === "cancelled"
      ? "This step was cancelled."
      : "This step failed. Open for the details.";
  }
  return copy.done;
}

function KnownToolBody({
  copy,
  ...props
}: ToolCallMessagePartProps & { copy: ToolCopy }) {
  const [open, setOpen] = useState(false);
  return (
    <ToolFallback.Root open={open} onOpenChange={setOpen}>
      <ToolFallback.Trigger
        toolName={copy.label}
        status={props.status}
        responseDeliveryStatus={props.responseDeliveryStatus}
      />
      <ToolFallback.Content>
        <p className="aui-tool-summary">{summary(copy, props)}</p>
        <ToolFallback.Error status={props.status} />
        <ToolFallback.Args argsText={props.argsText} />
        <ToolFallback.Result result={props.result} />
      </ToolFallback.Content>
    </ToolFallback.Root>
  );
}

function knownTool(name: string): ToolCallMessagePartComponent {
  const copy = TOOL_COPY[name];
  return (props) => {
    if (needsFallback(props)) return <ChatToolFallback {...props} />;
    return <KnownToolBody copy={copy} {...props} />;
  };
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null;
}

/** A plan is the call's own full list: the first step not done is the one in progress. */
const WritePlan: ToolCallMessagePartComponent = (props) => {
  if (needsFallback(props)) return <ChatToolFallback {...props} />;
  const args = isRecord(props.args) ? props.args : {};
  const items = (Array.isArray(args.items) ? args.items : [])
    .filter(isRecord)
    .filter((item) => typeof item.content === "string" && item.content.trim() !== "");
  if (items.length === 0) return <ChatToolFallback {...props} />;
  const steps = items.map((item, index) => ({
    id: typeof item.id === "string" ? item.id : String(index),
    label: displayText(item.content as string),
    description:
      typeof item.active_form === "string" && item.active_form.trim() !== ""
        ? displayText(item.active_form)
        : undefined,
  }));
  const open = items.findIndex((item) => item.status !== "completed");
  return (
    <AgentPlan
      title={typeof args.title === "string" ? args.title : undefined}
      steps={steps}
      activeIndex={open === -1 ? steps.length : open}
    />
  );
};

/** Module-scope backend entries: renderers only, no client execution and no new approvals. */
export const CHAT_TOOLKIT = defineToolkit({
  run_code: { type: "backend", render: knownTool("run_code") },
  get_financial_context: { type: "backend", render: knownTool("get_financial_context") },
  query_movements: { type: "backend", render: knownTool("query_movements") },
  summarize_spending: { type: "backend", render: knownTool("summarize_spending") },
  run_query: { type: "backend", render: knownTool("run_query") },
  project: { type: "backend", render: knownTool("project") },
  amortize: { type: "backend", render: knownTool("amortize") },
  compound: { type: "backend", render: knownTool("compound") },
  show_chart: { type: "backend", render: knownTool("show_chart") },
  show_table: { type: "backend", render: knownTool("show_table") },
  present: { type: "backend", render: knownTool("present") },
  record_movement: { type: "backend", render: knownTool("record_movement") },
  correct_movement: { type: "backend", render: knownTool("correct_movement") },
  update_setup: { type: "backend", render: knownTool("update_setup") },
  preference: { type: "backend", render: knownTool("preference") },
  import_document: { type: "backend", render: knownTool("import_document") },
  undo: { type: "backend", render: knownTool("undo") },
  // The task card stands on its own, not folded into the reasoning trace.
  delegate_task: { type: "backend", display: "standalone", render: DelegateTaskCard },
  write_plan: { type: "backend", render: WritePlan },
});
