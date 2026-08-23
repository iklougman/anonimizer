import type { AvailableApp } from "./types";

const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export async function listAvailableApps(accessToken: string): Promise<AvailableApp[]> {
  const response = await fetch(`${API_BASE_URL}/api/apps`, {
    headers: { Authorization: `Bearer ${accessToken}` },
  });
  if (!response.ok) throw new Error(`failed to list available apps: ${response.status}`);
  return response.json();
}
