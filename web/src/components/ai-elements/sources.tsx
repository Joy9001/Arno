import { useAuiState } from "@assistant-ui/react";
import { safeSourceUrl, type Citation } from "./inline-citation";
import { LedgerCitationPreview, useLedgerCitations } from "./ledger-citation";

export function useSources(): Citation[] {
  const parts = useAuiState((state) => state.message.parts);
  return parts.flatMap((part) => {
    if (part.type !== "source" || part.sourceType !== "url" || !safeSourceUrl(part.url)) return [];
    const metadata = part.providerMetadata?.arno;
    const snippet = metadata && typeof metadata.snippet === "string" ? metadata.snippet : undefined;
    return [{ id: part.id, url: part.url, title: part.title || part.url, snippet }];
  });
}

export function Sources() {
  const sources = useSources();
  const ledger = useLedgerCitations();
  if (!sources.length && !ledger.length) return null;
  return <details className="mt-3 text-sm"><summary className="cursor-pointer text-[var(--sub)]">Sources ({sources.length + ledger.length})</summary>
    {sources.length > 0 && <ol className="mt-2 list-inside list-decimal space-y-1">{sources.map((source) => <li key={source.id}><a href={source.url} target="_blank" rel="noopener noreferrer" className="text-[var(--lime)] underline">{source.title}</a>{source.snippet && <p className="text-[var(--sub)]">{source.snippet}</p>}</li>)}</ol>}
    {ledger.length > 0 && <ul className="mt-2 space-y-1">{ledger.map((source) => <li key={source.id}><LedgerCitationPreview source={source} /></li>)}</ul>}
  </details>;
}
