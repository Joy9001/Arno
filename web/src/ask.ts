export const ASK_EVENT = "fin-agent:ask";

/** Ask the global assistant from anywhere, without prop-drilling through every page. */
export function askAgent(text: string): void {
  window.dispatchEvent(new CustomEvent<string>(ASK_EVENT, { detail: text }));
}
