
/** The conversation's first load: a turn, a reply, and the composer's space. */
export function ConversationSkeleton() {
  return (
    <div className="skeleton-conversation" aria-busy="true" aria-label="Loading conversation">
      <div className="skeleton-turn user">
        <div className="sk sk-bubble-user" />
      </div>
      <div className="skeleton-turn assistant">
        <div className="sk sk-avatar" />
        <div className="skeleton-lines">
          <div className="sk sk-line wide" />
          <div className="sk sk-line" />
          <div className="sk sk-receipt" />
        </div>
      </div>
      <span className="sr-only">Loading conversation…</span>
    </div>
  );
}
