export const ASK_EVENT = "fin-agent:ask";
export const ATTACH_EVENT = "fin-agent:attach";

/** Ask the global assistant from anywhere, without prop-drilling through every page. */
export function askAgent(text: string): void {
  window.dispatchEvent(new CustomEvent<string>(ASK_EVENT, { detail: text }));
}

/** Open the assistant with these files already in the composer; the user sends them. */
export function attachAgent(files: File[]): void {
  window.dispatchEvent(new CustomEvent<File[]>(ATTACH_EVENT, { detail: files }));
}
