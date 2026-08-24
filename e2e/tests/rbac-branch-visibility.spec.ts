import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

// Proves the RBAC branch-visibility model end-to-end, across three
// simultaneous role sessions (doctor, staff, super_admin) sharing the one
// branch that provision_e2e_tenant.py (Task 3) assigns all three users to:
//   1. Doctor creates a conversation.
//   2. Staff (same branch, own-only default visibility) cannot see it yet.
//   3. super_admin grants staff's role conversations:read:branch via the
//      /admin/permissions UI.
//   4. Staff can now see -- and open, read-only -- the doctor's conversation.
test("a doctor's conversation is invisible to same-branch staff until conversations:read:branch is granted", async ({ browser }) => {
  const tenant = loadTenant();
  const { doctor, staff, super_admin } = tenant.users;

  // 1. Doctor creates a conversation.
  const doctorContext = await browser.newContext();
  const doctorPage = await doctorContext.newPage();
  await injectSession(doctorContext, await getRopcTokens(doctor.email, doctor.password));
  await doctorPage.goto("/apps/anonymization");
  await doctorPage.getByRole("button", { name: "Neue Anfrage" }).click();
  await doctorPage.waitForURL(/\/apps\/anonymization\/c\//);
  const conversationId = doctorPage.url().split("/").pop();
  await doctorContext.close();

  // 2. Staff (same branch, per provision_e2e_tenant.py) cannot see it yet --
  // own-only visibility is the default. This must be a real check of the
  // conversation *list*, not just "the page loaded". Sidebar rows
  // (components/ConversationSidebar.tsx) are plain `role="button"` divs with
  // no href/data attribute exposing the conversation id, so we key off the
  // one thing that's unique per-conversation in the DOM: the owner-email
  // badge (`conversation.owner_email.split("@")[0]`), which the sidebar only
  // renders for conversations that aren't the viewer's own. provision_e2e_
  // tenant.py's emails are `e2e-<role>-<run_marker>@example.test`, unique to
  // this run, so this can't false-match unrelated UI text.
  const doctorHandle = doctor.email.split("@")[0];
  const staffContext = await browser.newContext();
  const staffPage = await staffContext.newPage();
  await injectSession(staffContext, await getRopcTokens(staff.email, staff.password));
  await staffPage.goto("/apps/anonymization");
  await expect(staffPage.getByRole("button", { name: "Neue Anfrage" })).toBeVisible();
  // `getByText(doctorHandle).toHaveCount(0)` below is a web-first assertion:
  // it succeeds the instant zero matches are in the DOM, which is also true
  // of ConversationSidebar's pre-fetch loading-skeleton state (rendered
  // while `conversations === null`, before listConversations() -- a fetch
  // uncoordinated with the useMe() call the "Neue Anfrage" button above
  // depends on -- has resolved). Without waiting the skeleton away first, a
  // genuinely broken RBAC check could false-pass by sampling the DOM before
  // the conversations list request settles. Waiting for the skeleton to be
  // gone is a real signal the list actually loaded, not just "shows nothing
  // right now".
  await expect(staffPage.locator('[class*="skeletonList"]')).toBeHidden();
  await expect(staffPage.getByText(doctorHandle)).toHaveCount(0);
  await staffContext.close();

  // 3. super_admin grants staff conversations:read:branch.
  const adminContext = await browser.newContext();
  const adminPage = await adminContext.newPage();
  await injectSession(adminContext, await getRopcTokens(super_admin.email, super_admin.password));
  await adminPage.goto("/admin/permissions");
  await expect(adminPage.getByRole("heading", { name: "Berechtigungen" })).toBeVisible();

  // The permissions matrix's column order comes from the backend's
  // MATRIX_ROLES tuple (backend/app/auth/permissions.py), rendered via
  // GET /api/admin/permissions's `roles` array -- not guaranteed by this
  // spec. Rather than assume a fixed index (verified empirically to be
  // [doctor, staff] today, but brittle against future reordering), resolve
  // the "Personal" (staff) column by its header text.
  const headerTexts = await adminPage.locator("table thead th").allTextContents();
  const staffColumnIndex = headerTexts.findIndex((text) => text.includes("Personal"));
  // > 0 (not just >= 0): index 0 is the empty <th> above the permission-label
  // column (page.tsx's leading `<th></th>`), which can never legitimately be
  // "Personal" -- findIndex returning 0 here would mean the header text
  // wasn't found at all, same as -1, and should fail loudly rather than
  // silently index into the wrong column.
  expect(staffColumnIndex, `"Personal" column not found in header: ${JSON.stringify(headerTexts)}`).toBeGreaterThan(0);

  const branchRow = adminPage.locator("tr", { hasText: "Sichtbarkeit Filiale" });
  const staffCheckbox = branchRow.locator("td").nth(staffColumnIndex).locator("input[type=checkbox]");
  await expect(staffCheckbox).not.toBeChecked();
  await staffCheckbox.check();
  await adminPage.getByRole("button", { name: "Speichern" }).click();
  await expect(adminPage.getByText("Gespeichert.")).toBeVisible();
  await adminContext.close();

  // 4. Staff now sees the doctor's conversation in the sidebar (owner badge
  // present), and can open it read-only. `page.tsx`'s isReadOnly banner
  // ("Schreibgeschützt — Unterhaltung von {owner_email}") only renders when
  // the backend actually returned the conversation with is_own: false --
  // this specifically proves the *doctor's* conversation was fetched, not
  // just that some page rendered.
  //
  // This only asserts "at least one" badge, not an exact count: the doctor
  // account is shared across spec files against the one ephemeral tenant
  // global-setup.ts provisions per full run, and other specs (e.g.
  // chat-roundtrip.spec.ts) create their own conversations for the same
  // doctor. When those run in the same suite invocation, staff's newly
  // granted branch-wide visibility surfaces all of them, each carrying the
  // same owner-handle badge text, so the count is no longer guaranteed to be
  // 1. The specific-conversation check below (navigating to `conversationId`
  // captured in step 1, then checking the read-only banner + doctor's email)
  // already proves *this* conversation resolves correctly for staff, so the
  // visibility claim stays fully covered.
  const staffContext2 = await browser.newContext();
  const staffPage2 = await staffContext2.newPage();
  await injectSession(staffContext2, await getRopcTokens(staff.email, staff.password));
  await staffPage2.goto("/apps/anonymization");
  await expect(staffPage2.getByText(doctorHandle).first()).toBeVisible();
  await staffPage2.goto(`/apps/anonymization/c/${conversationId}`);
  await expect(staffPage2.getByText(/Schreibgeschützt/)).toBeVisible();
  await expect(staffPage2.getByText(doctor.email)).toBeVisible();
  await staffContext2.close();
});
