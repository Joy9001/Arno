import type { ToolCallMessagePartComponent } from "@assistant-ui/react";

export const ToolFallback: ToolCallMessagePartComponent = ({
  toolName,
  status,
  approval,
}) => (
  <p
    className="aui-part-note"
    role={status.type === "incomplete" ? "status" : undefined}
  >
    {toolName.replaceAll("_", " ")}:{" "}
    {approval?.approved === false
      ? "Denied"
      : status.type === "running"
        ? "Running…"
        : status.type === "incomplete"
          ? "Did not finish"
          : "Finished"}
  </p>
);
