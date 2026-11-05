import { Skeleton } from "@radix-ui/themes";

/** The conversation's first load: a turn, a reply, and the composer's space. */
export function ConversationSkeleton() {
  return (
    <div className="skeleton-conversation" aria-busy="true" aria-label="Loading conversation">
      <div className="skeleton-turn user">
        <Skeleton className="sk sk-bubble-user" />
      </div>
      <div className="skeleton-turn assistant">
        <Skeleton className="sk sk-avatar" />
        <div className="skeleton-lines">
          <Skeleton className="sk sk-line wide" />
          <Skeleton className="sk sk-line" />
          <Skeleton className="sk sk-receipt" />
        </div>
      </div>
      <span className="sr-only">Loading conversation…</span>
    </div>
  );
}
