const API_BASE_URL = process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://localhost:8000";

export class SignupApiError extends Error {
  status: number;

  constructor(status: number, message: string) {
    super(message);
    this.name = "SignupApiError";
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

export interface TenantSignupIn {
  practice_name: string;
  first_name: string;
  last_name: string;
  email: string;
  password: string;
}

export interface TenantSignupOut {
  tenant_id: string;
  verification_email_sent: boolean;
}

export async function createTenantSignup(body: TenantSignupIn): Promise<TenantSignupOut> {
  const response = await fetch(`${API_BASE_URL}/api/signup`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  });
  if (!response.ok) {
    throw new SignupApiError(response.status, await parseErrorDetail(response, "Registrierung fehlgeschlagen."));
  }
  return response.json();
}
