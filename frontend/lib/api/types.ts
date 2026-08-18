export interface ConversationSummary {
  id: string;
  title: string | null;
  created_at: string;
  updated_at: string;
  owner_user_id: string;
  owner_email: string;
  is_own: boolean;
}

export type ConversationDetail = ConversationSummary;

export interface MessageOut {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
}

export interface Me {
  user_id: string;
  tenant_id: string;
  tenant_name: string;
  email: string;
  role: "super_admin" | "doctor" | "staff";
  branch_id: string | null;
  branch_name: string | null;
  permissions: string[];
}
