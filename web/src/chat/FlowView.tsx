import { useEffect, useMemo } from "react";
import { useAuiState } from "@assistant-ui/react";
import {
  Background,
  Controls,
  Position,
  ReactFlow,
  ReactFlowProvider,
  useReactFlow,
  type Edge,
  type Node,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";
import { Orb } from "../Orb";
import { hasToolWidget } from "./widgets/results";
import { ToolWidgets, AffordabilityLab } from "./widgets";
import { parseProjectionReceipt } from "../projectionReceipt";
import "./flow-graph.css";

type Part = {
  type: string;
  text?: string;
  toolName?: string;
  args?: unknown;
  result?: unknown;
  data?: unknown;
  name?: string;
  status?: { type?: string };
};
type Message = {
  id: string;
  role: string;
  content: readonly Part[];
  metadata?: { custom?: { server_id?: string } };
};

function Graph({
  fit,
  onOpenChat,
}: {
  fit: boolean;
  onOpenChat?: (id: string) => void;
}) {
  const messages = useAuiState(
    (state) => state.thread.messages,
  ) as unknown as readonly Message[];
  const { fitView } = useReactFlow();
  const reducedMotion = window.matchMedia(
    "(prefers-reduced-motion: reduce)",
  ).matches;
  const { nodes, edges } = useMemo(() => {
    const nodes: Node[] = [];
    const edges: Edge[] = [];
    let turn = -1;
    let column = 0;
    let previous: string | undefined;
    for (const message of messages) {
      if (message.role === "user") {
        turn += 1;
        column = 0;
        previous = undefined;
        const id = `prompt-${message.id}`;
        const prompt = message.content
          .filter((part) => part.type === "text")
          .map((part) => part.text)
          .join(" ");
        nodes.push({
          id,
          position: { x: 0, y: turn * 700 },
          data: {
            label: (
              <div>
                <Orb size={36} />
                <strong>Prompt</strong>
                <p>{prompt || "Attachment"}</p>
              </div>
            ),
          },
        });
        previous = id;
        continue;
      }
      if (message.role !== "assistant") continue;
      if (turn < 0) turn = 0;
      for (const [index, part] of message.content.entries()) {
        if (part.type !== "tool-call") continue;
        const id = `${message.id}-${index}`;
        column += 1;
        const title = part.toolName?.replaceAll("_", " ") ?? "Tool";
        nodes.push({
          id,
          position: { x: column * 440, y: turn * 700 },
          data: {
            label: (
              <div>
                <strong>{title}</strong>
                <p>
                  {part.status?.type === "running"
                    ? "Running…"
                    : part.status?.type === "incomplete"
                      ? "Stopped"
                      : "Complete"}
                </p>
              </div>
            ),
          },
        });
        if (previous)
          edges.push({
            id: `${previous}-${id}`,
            source: previous,
            target: id,
            animated: !reducedMotion,
            style: { stroke: column % 2 ? "var(--lime)" : "var(--you)" },
          });
        previous = id;
        if (part.toolName && hasToolWidget(part.toolName, part.result)) {
          const output = `${id}-output`;
          nodes.push({
            id: output,
            position: { x: column * 440 + 240, y: turn * 700 + 180 },
            style: { width: 380 },
            data: {
              label: (
                <div className="nodrag nopan">
                  <ToolWidgets
                    toolName={part.toolName}
                    args={part.args}
                    result={part.result}
                  />
                </div>
              ),
            },
          });
          edges.push({
            id: `${id}-${output}`,
            source: id,
            target: output,
            animated: !reducedMotion,
            style: { stroke: "var(--you)" },
          });
        }
      }
      for (const [index, part] of message.content.entries()) {
        if (part.type !== "data" || part.name !== "card") continue;
        const projection = parseProjectionReceipt(part.data);
        if (!projection) continue;
        const id = `${message.id}-card-${index}`;
        column += 1;
        nodes.push({
          id,
          position: { x: column * 440, y: turn * 700 + 180 },
          style: { width: 380 },
          data: {
            label: (
              <div className="nodrag nopan">
                <AffordabilityLab seed={projection} />
              </div>
            ),
          },
        });
        if (previous)
          edges.push({
            id: `${previous}-${id}`,
            source: previous,
            target: id,
            animated: !reducedMotion,
            style: { stroke: "var(--you)" },
          });
      }
      const answer = message.content
        .filter((part) => part.type === "text")
        .map((part) => part.text)
        .join(" ");
      if (answer) {
        const id = `answer-${message.id}`;
        column += 1;
        const target = message.metadata?.custom?.server_id ?? message.id;
        nodes.push({
          id,
          position: { x: column * 440, y: turn * 700 },
          data: {
            label: (
              <button type="button" onClick={() => onOpenChat?.(target)}>
                <strong>Answer</strong>
                <p>
                  {answer.slice(0, 160)}
                  {answer.length > 160 ? "…" : ""}
                </p>
                <span>Open in chat</span>
              </button>
            ),
          },
        });
        if (previous)
          edges.push({
            id: `${previous}-${id}`,
            source: previous,
            target: id,
            animated: !reducedMotion,
            style: { stroke: "var(--lime)" },
          });
        previous = id;
      }
    }
    return { nodes: nodes.map((node) => ({ ...node, sourcePosition: Position.Right, targetPosition: Position.Left })), edges };
  }, [messages, onOpenChat, reducedMotion]);
  useEffect(() => {
    void fitView({ padding: 0.2 });
  }, [fit, fitView]);
  if (!nodes.length)
    return (
      <div className="flow-empty">
        <Orb size={56} />
        <p>Ask something in Chat and its work shows up here as a graph.</p>
      </div>
    );
  return (
    <div className="flow-graph" style={{ height: "100%", minHeight: 400 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        fitView
        nodesDraggable={false}
        nodesConnectable={false}
        aria-label="Conversation flow"
      >
        <Background />
        <Controls />
      </ReactFlow>
    </div>
  );
}

export function FlowView({
  fit = false,
  onOpenChat,
}: {
  fit?: boolean;
  onOpenChat?: (messageId: string) => void;
}) {
  return (
    <ReactFlowProvider>
      <Graph fit={fit} onOpenChat={onOpenChat} />
    </ReactFlowProvider>
  );
}
