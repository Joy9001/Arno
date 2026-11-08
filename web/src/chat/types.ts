import type { UIMessage } from "ai";

export type ChatMetadata = {
  server_id?: string;
  flagged_at?: string | null;
};

/** Wire data parts. `card` stays open because the server emits many card kinds. */
export type ChatDataParts = {
  card: Record<string, unknown>;
  "number-flags": { amounts: string[] };
  "model-badge": { model: string };
  "reply-model": { model: string; fallback: boolean };
  ending: { note: string };
  statement: {
    attachment_id: string;
    filename: string;
    detected_kind?: string;
  };
  photo: {
    attachment_id: string;
    filename: string;
  };
};

export type ChatMessage = UIMessage<ChatMetadata, ChatDataParts>;

export type ThreadSummary = {
  id: string;
  title: string;
  updated_at: string;
  message_count: number;
  snippet: string | null;
  running: boolean;
  flagged: boolean;
};

export type ThreadList = {
  threads: ThreadSummary[];
  as_of: string;
};

export type ThreadBody = {
  thread: {
    id: string;
    title: string;
    flagged: boolean;
    snapshot_at: string | null;
  };
  messages: ChatMessage[];
  running: boolean;
  as_of: string;
};

export type Preference = {
  id: string;
  body: string;
  kind: "preference" | "fact" | "rule";
  created_by: "agent" | "user";
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type PreferenceList = {
  preferences: Preference[];
  as_of: string;
};
