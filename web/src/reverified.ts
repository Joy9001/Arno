let reverifiedUntil = 0;

/** Whether a passkey check in this tab still covers a step-up action. */
export function reverifiedNow(): boolean {
  return Date.now() < reverifiedUntil;
}

/** Remember a successful passkey check for the server's five-minute step-up window. */
export function markReverified(): void {
  reverifiedUntil = Date.now() + 4 * 60 * 1000;
}

/** A signed-out tab must not carry freshness into the next session. */
export function clearReverified(): void {
  reverifiedUntil = 0;
}
