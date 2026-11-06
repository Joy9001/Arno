const OUTSIDE_MARKER = /<\/?outside>/g;
const VISIBLE_TEXT_NODES = new Set(["text", "inlineCode", "code"]);

type MarkdownNode = {
  type?: string;
  value?: unknown;
  children?: MarkdownNode[];
};

/**
 * Hide the internal <outside> envelope from what a reader sees. Only those
 * literal markers are removed: the text between them, other HTML, and the
 * saved message or result itself stay untouched.
 */
export function displayText(value: string): string {
  return value.replace(OUTSIDE_MARKER, "");
}

function stripNode(node: MarkdownNode): void {
  if (
    typeof node.value === "string" &&
    typeof node.type === "string" &&
    VISIBLE_TEXT_NODES.has(node.type)
  ) {
    node.value = displayText(node.value);
  }
  for (const child of node.children ?? []) stripNode(child);
}

/** A remark transformer, so markers in prose, inline code and table cells all go. */
export function remarkStripOutsideMarkers() {
  return (tree: unknown) => {
    stripNode(tree as MarkdownNode);
  };
}
