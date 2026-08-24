import * as fs from "fs";
import * as path from "path";

export interface E2EUser {
  user_id: string;
  email: string;
  password: string;
  keycloak_subject: string;
}

export interface E2ETenant {
  tenant_id: string;
  branch_id: string;
  users: {
    super_admin: E2EUser;
    doctor: E2EUser;
    staff: E2EUser;
  };
}

const TENANT_FILE = path.resolve(__dirname, "..", ".e2e-tenant.json");

export function loadTenant(): E2ETenant {
  if (!fs.existsSync(TENANT_FILE)) {
    throw new Error(
      `${TENANT_FILE} not found -- did globalSetup run? (npm test runs it automatically; ` +
      `if you're debugging a single test file directly, run \`npm test\` once first)`
    );
  }
  return JSON.parse(fs.readFileSync(TENANT_FILE, "utf-8"));
}

export function tenantFilePath(): string {
  return TENANT_FILE;
}
