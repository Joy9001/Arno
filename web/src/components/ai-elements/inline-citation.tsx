import { HoverCard } from "radix-ui";
import { useId, useState, type ReactNode } from "react";

export type Citation = {
  id: string;
  url: string;
  title: string;
  snippet?: string;
};

export function safeSourceUrl(url: string): boolean {
  try {
    return ["http:", "https:"].includes(new URL(url).protocol);
  } catch {
    return false;
  }
}

export function InlineCitation({
  source,
  children,
}: {
  source: Citation;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(false);
  const descriptionId = useId();
  return (
    <HoverCard.Root open={open} onOpenChange={setOpen} openDelay={200}>
      <HoverCard.Trigger asChild>
        <button
          type="button"
          aria-expanded={open}
          aria-describedby={descriptionId}
          onClick={() => setOpen(!open)}
          onKeyDown={(event) => { if (event.key === "Escape") setOpen(false); }}
          className="text-[var(--lime)] underline underline-offset-2"
        >
          {children}
        </button>
      </HoverCard.Trigger>
      <span id={descriptionId} className="sr-only">{source.title}{source.snippet ? `. ${source.snippet}` : ""}. Open Sources for the link.</span>
      <HoverCard.Portal>
        <HoverCard.Content
          sideOffset={6}
          className="z-50 max-w-xs rounded-xl border border-white/10 bg-[var(--surface-2)] p-3 text-sm text-[var(--text)] shadow-xl"
        >
          <strong>{source.title}</strong>
          {source.snippet && (
            <p className="mt-1 text-[var(--sub)]">{source.snippet}</p>
          )}
          <a
            href={source.url}
            target="_blank"
            rel="noopener noreferrer"
            className="mt-1 block break-all text-xs text-[var(--lime)] underline"
          >
            Open source
          </a>
        </HoverCard.Content>
      </HoverCard.Portal>
    </HoverCard.Root>
  );
}
