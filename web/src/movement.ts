import type { Movement } from "./types";

/**
 * Bank narrations carry UPI references and terminal ids. The payee is the human name the
 * categoriser already extracted; a transfer has no payee, so its two sides are the name.
 */
export function movementLabel(movement: Movement): string {
  if (movement.payee_name) return movement.payee_name;
  const first = movement.transfers[0];
  if (movement.kind === "transfer" && first) return `${first.from_name} → ${first.to_name}`;
  return movement.description;
}

/** The line under the label: the category, or what the transfer did. */
export function movementNote(movement: Movement): string {
  if (movement.kind === "transfer") {
    return movement.transfers.length > 1
      ? `${movement.transfers.length} legs`
      : "Between accounts";
  }
  return movement.category_name ?? humanizeKind(movement.kind);
}

export function humanizeKind(kind: string): string {
  return kind.charAt(0).toUpperCase() + kind.slice(1);
}
