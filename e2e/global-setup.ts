import { execFileSync } from "child_process";
import * as fs from "fs";
import * as path from "path";

async function globalSetup(): Promise<void> {
  const backendDir = path.resolve(__dirname, "..", "backend");
  const output = execFileSync(
    "python", ["scripts/provision_e2e_tenant.py", "create"],
    { cwd: backendDir, encoding: "utf-8", env: process.env }
  );
  const tenantFile = path.resolve(__dirname, ".e2e-tenant.json");
  fs.writeFileSync(tenantFile, output.trim());
  console.log(`e2e: provisioned tenant, credentials written to ${tenantFile}`);
}

export default globalSetup;
