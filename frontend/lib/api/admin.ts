import type { AdminUser, AdminUserCreated, App, Branch, PermissionMatrix } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class AdminApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "AdminApiError";
    this.status = status;
  }
}

async function parseErrorDetail(response: Response, fallback: string): Promise<string> {
  try {
    const body = await response.json();
    if (typeof body.detail === "string") return body.detail;
  } catch {
    // response body wasn't JSON; keep the fallback
  }
  return fallback;
}

// --- Branches ---------------------------------------------------------------

export async function listBranches(accessToken: string): Promise<Branch[]> {
  const response = await fetch(`${API_BASE_URL}/api/admin/branches`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to list branches"));
  return response.json();
}

export async function createBranch(accessToken: string, name: string): Promise<Branch> {
  const response = await fetch(`${API_BASE_URL}/api/admin/branches`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to create branch"));
  return response.json();
}

export async function updateBranch(accessToken: string, branchId: string, name: string): Promise<Branch> {
  const response = await fetch(`${API_BASE_URL}/api/admin/branches/${branchId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ name }),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to rename branch"));
  return response.json();
}

export async function deleteBranch(accessToken: string, branchId: string): Promise<void> {
  const response = await fetch(`${API_BASE_URL}/api/admin/branches/${branchId}`, {
    method: "DELETE",
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok && response.status !== 204) {
    throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to delete branch"));
  }
}

// --- Users --------------------------------------------------------------

export async function listUsers(accessToken: string): Promise<AdminUser[]> {
  const response = await fetch(`${API_BASE_URL}/api/admin/users`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to list users"));
  return response.json();
}

export interface CreateUserInput {
  email: string;
  role: string;
  branch_id?: string | null;
  first_name?: string;
  last_name?: string;
  keycloak_subject?: string;
}

export async function createUser(accessToken: string, input: CreateUserInput): Promise<AdminUserCreated> {
  const response = await fetch(`${API_BASE_URL}/api/admin/users`, {
    method: "POST",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to create user"));
  return response.json();
}

export interface UpdateUserInput {
  role?: string;
  branch_id?: string | null;
  is_active?: boolean;
}

export async function updateUser(
  accessToken: string,
  userId: string,
  input: UpdateUserInput
): Promise<AdminUser> {
  const response = await fetch(`${API_BASE_URL}/api/admin/users/${userId}`, {
    method: "PATCH",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to update user"));
  return response.json();
}

// --- Permission matrix -----------------------------------------------

export async function getPermissionMatrix(accessToken: string): Promise<PermissionMatrix> {
  const response = await fetch(`${API_BASE_URL}/api/admin/permissions`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to load permissions"));
  return response.json();
}

export async function putPermissionMatrix(
  accessToken: string,
  matrix: Record<string, Record<string, boolean>>
): Promise<PermissionMatrix> {
  const response = await fetch(`${API_BASE_URL}/api/admin/permissions`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify({ matrix }),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to save permissions"));
  return response.json();
}

// --- Apps -----------------------------------------------------------

export async function listApps(accessToken: string): Promise<App[]> {
  const response = await fetch(`${API_BASE_URL}/api/admin/apps`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to list apps"));
  return response.json();
}

export async function putAppAssignment(
  accessToken: string,
  appId: string,
  input: { branch_id: string | null; is_enabled: boolean }
): Promise<App["assignments"][number]> {
  const response = await fetch(`${API_BASE_URL}/api/admin/apps/${appId}/assignment`, {
    method: "PUT",
    headers: { Authorization: `Bearer ${accessToken}`, "Content-Type": "application/json" },
    body: JSON.stringify(input),
  });
  if (!response.ok) throw new AdminApiError(response.status, await parseErrorDetail(response, "failed to update app assignment"));
  return response.json();
}
