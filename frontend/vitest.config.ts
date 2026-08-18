import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    // Vite (which Vitest runs on) does not read tsconfig.json's "paths" the
    // way Next.js's own bundler does -- this alias must be declared here too,
    // or any test importing a "@/..." module fails to resolve at runtime.
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },
  test: {
    environment: "jsdom",
    setupFiles: ["./vitest.setup.ts"],
    // Deterministic test values -- lib/auth.ts reads these at import time, so
    // tests must not depend on the shell's env or a local .env file being sourced.
    env: {
      KEYCLOAK_ISSUER: "http://localhost:8080/realms/chatgpt-proxy-dev",
      KEYCLOAK_INTERNAL_URL: "http://keycloak:8080/realms/chatgpt-proxy-dev",
      KEYCLOAK_CLIENT_ID: "chatgpt-proxy-frontend",
    },
  },
});
