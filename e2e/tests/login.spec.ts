import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";

test.describe("login", () => {
  test("unauthenticated request redirects to Keycloak's hosted login, and a successful login lands on an authenticated page", async ({ page }) => {
    const tenant = loadTenant();
    const doctor = tenant.users.doctor;

    await page.goto("/dashboard");

    // middleware.ts redirects to /api/auth/signin. The app configures no
    // custom `pages.signIn` (see frontend/lib/auth.ts), so NextAuth renders
    // its own built-in provider-chooser page there -- a "Sign in with
    // Keycloak" button, not an automatic forward. Click through it to reach
    // Keycloak's own hosted login form; assert we land there, not on a
    // custom login UI (there isn't one).
    await page.waitForURL(/\/api\/auth\/signin/);
    await page.getByRole("button", { name: "Sign in with Keycloak" }).click();
    await page.waitForURL(/\/realms\/chatgpt-proxy-dev\/protocol\/openid-connect\/auth/);

    await page.fill("#username", doctor.email);
    await page.fill("#password", doctor.password);
    await page.click("#kc-login");

    await page.waitForURL(/\/dashboard/);
    await expect(page.getByRole("heading", { name: "Dashboard" })).toBeVisible();
  });

  test("an unauthenticated request to a protected route redirects to sign-in", async ({ browser }) => {
    // Fresh, cookie-free context -- do not reuse the logged-in context above.
    const context = await browser.newContext();
    const page = await context.newPage();

    await page.goto("/admin");

    await page.waitForURL(/\/api\/auth\/signin/);
    expect(page.url()).toContain("callbackUrl");

    await context.close();
  });
});
