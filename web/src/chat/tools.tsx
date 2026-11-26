import {
  defineToolkit,
  type ToolCallMessagePartComponent,
} from "@assistant-ui/react";
import { ChatToolFallback } from "./parts";
import { ToolWidgets } from "./widgets";
import { hasToolWidget } from "./widgets/results";

const Tool: ToolCallMessagePartComponent = (props) => {
  if (props.approval || props.status.type === "requires-action")
    return <ChatToolFallback {...props} />;
  if (
    (props.toolName === "run_query" || props.toolName === "run_python") &&
    hasToolWidget(props.toolName, props.result)
  ) {
    return (
      <ToolWidgets
        toolName={props.toolName}
        args={props.args}
        result={props.result}
      />
    );
  }
  return <ChatToolFallback {...props} />;
};

export const CHAT_TOOLKIT = defineToolkit({
  run_query: { type: "backend", display: "standalone", render: Tool },
  project: { type: "backend", display: "standalone", render: Tool },
  record_movements: { type: "backend", display: "standalone", render: Tool },
  correct_movement: { type: "backend", display: "standalone", render: Tool },
  update_setup: { type: "backend", display: "standalone", render: Tool },
  undo: { type: "backend", display: "standalone", render: Tool },
  run_python: { type: "backend", display: "standalone", render: Tool },
  web_search: { type: "backend", display: "standalone", render: Tool },
});
