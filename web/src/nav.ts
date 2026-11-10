import { createContext } from "react";
import type { User } from "./types";

export type PageId = "ask" | "chats" | "money" | "activity" | "recaps" | "settings";

export const NAVIGATE_EVENT = "arno:navigate";

/** Move to an area (and optional sub-route) from anywhere, without prop-drilling. */
export function goTo(page: PageId, sub?: string): void {
  window.dispatchEvent(new CustomEvent(NAVIGATE_EVENT, { detail: { page, sub } }));
}

/** The signed-in person, for greetings. */
export const UserContext = createContext<User | null>(null);
