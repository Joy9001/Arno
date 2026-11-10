export const ASK_EVENT = "arno:ask";
export const ATTACH_EVENT = "arno:attach";

/** Ask the global assistant from anywhere, without prop-drilling through every page. */
export function askAgent(text: string, options: { send?: boolean } = {}): void {
  window.dispatchEvent(
    new CustomEvent<AskDetail>(ASK_EVENT, { detail: { text, send: Boolean(options.send) } }),
  );
}

export type AskDetail = { text: string; send: boolean };

/** Open the assistant with these files already in the composer; the user sends them. */
export function attachAgent(files: File[]): void {
  window.dispatchEvent(new CustomEvent<File[]>(ATTACH_EVENT, { detail: files }));
}
