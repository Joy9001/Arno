import type { ProjectionPayload } from "./projection";

export const EVIDENCE_EVENT = "fin-agent:evidence";

/** Open an answer's sources beside the conversation without drilling props through chat. */
export function openEvidence(payload: ProjectionPayload): void {
  window.dispatchEvent(new CustomEvent<ProjectionPayload>(EVIDENCE_EVENT, { detail: payload }));
}

export const PLANNER_EVENT = "fin-agent:planner";

/** Open the purchase planner on the canvas, starting from this projection's purchase. */
export function openPlanner(payload: ProjectionPayload): void {
  window.dispatchEvent(new CustomEvent<ProjectionPayload>(PLANNER_EVENT, { detail: payload }));
}
