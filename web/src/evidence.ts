import type { ProjectionPayload } from "./projection";

export const EVIDENCE_EVENT = "arno:evidence";

/** Open an answer's sources beside the conversation without drilling props through chat. */
export function openEvidence(payload: ProjectionPayload): void {
  window.dispatchEvent(new CustomEvent<ProjectionPayload>(EVIDENCE_EVENT, { detail: payload }));
}

export const RUN_EVENT = "arno:run";

/** Open a subagent run on the canvas beside the conversation. */
export function openRun(taskId: string): void {
  window.dispatchEvent(new CustomEvent<string>(RUN_EVENT, { detail: taskId }));
}

export const PLANNER_EVENT = "arno:planner";

/** Open the purchase planner on the canvas, starting from this projection's purchase. */
export function openPlanner(payload: ProjectionPayload): void {
  window.dispatchEvent(new CustomEvent<ProjectionPayload>(PLANNER_EVENT, { detail: payload }));
}
