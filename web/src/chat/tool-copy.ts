export type ToolCopy = {
  label: string;
  running: string;
  done: string;
};

export const TOOL_COPY: Record<string, ToolCopy> = {
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
  schedule_payment: {
    label: "Matching a schedule payment",
    running: "Updating the payment match…",
    done: "Updated the payment match.",
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

/** The short activity label the run row shows for a tool call. */
export function toolLabel(toolName: string): string {
  const label = TOOL_COPY[toolName]?.label;
  if (label) return label;
  const words = toolName.replaceAll("_", " ").trim();
  return words ? words[0]!.toUpperCase() + words.slice(1) : "Working";
}
