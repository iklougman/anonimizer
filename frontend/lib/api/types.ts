export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
}

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}
