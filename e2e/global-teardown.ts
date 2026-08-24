import { execFileSync } from "child_process";
import * as path from "path";
import { loadTenant, tenantFilePath } from "./support/tenant";
import * as fs from "fs";

async function globalTeardown(): Promise<void> {
  const backendDir = path.resolve(__dirname, "..", "backend");
  let tenant;
  try {
    tenant = loadTenant();
  } catch {
    console.warn("e2e: no tenant file found at teardown, nothing to clean up");
    return;
  }
  execFileSync(
    "python", ["scripts/provision_e2e_tenant.py", "cleanup", "--tenant-id", tenant.tenant_id],
    { cwd: backendDir, encoding: "utf-8", env: process.env }
  );
  const file = tenantFilePath();
  if (fs.existsSync(file)) fs.unlinkSync(file);
  console.log(`e2e: cleaned up tenant ${tenant.tenant_id}`);
}

export default globalTeardown;
