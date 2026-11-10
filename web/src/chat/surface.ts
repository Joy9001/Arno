import { createContext } from "react";
import { APICallError } from "ai";
import type { ProjectionPayload } from "../projection";

export type ChatSurfaceState = {
  capture:
    | { kind: "uploading" | "transcribing" | "recording"; message: string }
    | { kind: "error"; message: string }
    | null;
  onRetryCapture?: (kind: string, attachmentId: string, messageId: string) => void;
  onEvidence?: (payload: ProjectionPayload) => void;
  onStop?: (threadId: string) => void;
};

export const ChatSurfaceContext = createContext<ChatSurfaceState>({ capture: null });

/** One short sentence for the user; provider payloads stay out of the transcript. */
export function chatErrorMessage(error: Error): string {
  if (APICallError.isInstance(error)) {
    if (error.statusCode === 401) {
      return "Your session expired. Reload the page and sign in again.";
    }
    if (error.isRetryable) {
      return "Arno could not be reached or is temporarily unavailable. Try again.";
    }
  }
  return "Arno could not finish that answer. Your earlier messages are still here. Try again.";
}
