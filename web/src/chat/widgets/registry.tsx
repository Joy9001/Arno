import { renderGenerativeUI, type GenerativeUILibrary } from "@assistant-ui/generative-ui/react";
import { z } from "zod";
import { ComparisonMatrix, InteractiveChart, MetricHero, WaterfallChart } from "./visuals";
import { scenarios } from "./results";

const chartRow = z.object({ label: z.string(), amount: z.number().int().safe() });
const waterfallRow = chartRow.extend({ from: z.number().int().safe(), to: z.number().int().safe() });
const library: GenerativeUILibrary = {
  MetricHero: { description: "Ledger metric", properties: z.object({ label: z.string(), amount: z.number().int().safe(), baseline: z.number().int().safe().optional(), queryId: z.string() }), render: MetricHero },
  InteractiveChart: { description: "Ledger breakdown", properties: z.object({ rows: z.array(chartRow) }), render: InteractiveChart },
  WaterfallChart: { description: "Ledger changes", properties: z.object({ rows: z.array(waterfallRow) }), render: WaterfallChart },
  ComparisonMatrix: { description: "Computed scenarios", properties: z.object({ data: scenarios }), render: ComparisonMatrix },
};

/** The application selects these nodes from validated tool output, never model-provided UI props. */
export function renderWidget(node: unknown) {
  return renderGenerativeUI(node, library);
}
