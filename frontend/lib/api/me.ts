import type { Me } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function getMe(accessToken: string): Promise<Me> {
  const response = await fetch(`${API_BASE_URL}/api/me`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to load current user: ${response.status}`);
  return response.json();
}
