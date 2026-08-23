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

export type Role = "super_admin" | "doctor" | "staff";

export interface Branch {
  id: string;
  name: string;
  user_count: number;
  created_at: string;
}

export interface AdminUser {
  id: string;
  email: string;
  role: Role;
  branch_id: string | null;
  is_active: boolean;
  created_at: string;
}

export interface AdminUserCreated extends AdminUser {
  invite_email_sent: boolean;
}

export interface PermissionMatrix {
  roles: string[];
  permissions: string[];
  defaults: Record<string, string[]>;
  matrix: Record<string, Record<string, boolean>>;
}

export interface AppAssignment {
  branch_id: string | null;
  branch_name: string | null;
  is_enabled: boolean;
}

export interface App {
  id: string;
  key: string;
  name: string;
  description: string | null;
  is_entitled: boolean;
  assignments: AppAssignment[];
}

/** Lean, non-admin shape from GET /api/apps: what an ordinary user can see
 * about an app they're already entitled+enabled to use. */
export interface AvailableApp {
  key: string;
  name: string;
  description: string | null;
}
