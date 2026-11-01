import type { UIMessage } from "ai";

export type ChatMetadata = {
  server_id?: string;
  flagged_at?: string | null;
};

export type ChatMessage = UIMessage<ChatMetadata>;

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
  created_by: "agent" | "user";
  active: boolean;
  created_at: string;
  updated_at: string;
};

export type PreferenceList = {
  preferences: Preference[];
  as_of: string;
};
