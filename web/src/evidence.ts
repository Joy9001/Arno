import type { ProjectionPayload } from "./projection";

export const EVIDENCE_EVENT = "fin-agent:evidence";

/** Open an answer's sources beside the conversation without drilling props through chat. */
export function openEvidence(payload: ProjectionPayload): void {
  window.dispatchEvent(new CustomEvent<ProjectionPayload>(EVIDENCE_EVENT, { detail: payload }));
}
