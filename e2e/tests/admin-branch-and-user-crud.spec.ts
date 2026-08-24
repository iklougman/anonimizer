import { test, expect, type Locator, type Page } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

// Playwright has no getByDisplayValue() locator (only getByTestId,
// getByAltText, getByLabel, getByPlaceholder, getByText, getByTitle,
// getByRole) -- confirmed against the installed @playwright/test@1.62.1 API
// surface. AdminBranchesPage's rename <input> has no label/placeholder/role
// that identifies *which* branch it belongs to, so we find the right row by
// reading each rename input's live value via locator.inputValue() (the real
// DOM property, not a static attribute) until one matches. This also can't
// rely on row position: GET /api/admin/branches (backend/app/db/
// repositories/branch_repository.py) orders by Branch.name, so a freshly
// created/renamed branch is not reliably the first or last row, especially
// after a reload changes its alphabetical position relative to the rename.
async function findRenameInputIndex(page: Page, name: string): Promise<number> {
  const inputs = page.locator("table tbody tr td input");
  const count = await inputs.count();
  for (let i = 0; i < count; i++) {
    if ((await inputs.nth(i).inputValue()) === name) return i;
  }
  return -1;
}

// Not just position-independent (see above) but also retry-safe: right
// after page.goto()/page.reload(), AdminBranchesPage's useEffect-driven
// listBranches() fetch hasn't resolved yet (it depends on useSession()
// resolving first), so a single one-shot DOM scan can genuinely run before
// any rows exist -- this isn't a hypothetical, it reproduced empty results
// in a disposable diagnostic run against this exact helper. expect.poll
// retries the scan (using the suite's configured expect timeout) the same
// way Playwright's built-in locators auto-retry, instead of sampling the
// DOM exactly once.
async function locateBranchRenameInputByValue(page: Page, name: string): Promise<Locator> {
  await expect
    .poll(async () => (await findRenameInputIndex(page, name)) >= 0, {
      message: `waiting for a branch rename <input> with value "${name}"`,
    })
    .toBe(true);
  const index = await findRenameInputIndex(page, name);
  return page.locator("table tbody tr td input").nth(index);
}

// Proves the admin CRUD journey end-to-end: super_admin creates a branch,
// renames it, then creates a user assigned to the *renamed* branch (by
// label -- proving the branch created above is the one actually used, not
// just that some branch got attached), and finally changes that user's
// role.
//
// Both state-changing checks below (branch rename, user role change) are
// tied to the real PATCH request/response round-trip via
// page.waitForResponse + a reload, not to the optimistic local component
// state that updates the DOM before the network call resolves:
//   - AdminBranchesPage's rename <input> (frontend/app/(shell)/admin/
//     branches/page.tsx) is a controlled input whose displayed value
//     (`renaming[branch.id] ?? branch.name`) follows the onChange handler
//     the instant you type, independent of whether updateBranch()'s PATCH
//     (fired on blur) has resolved. Asserting on the input's value right
//     after blur() would only prove what was typed, not what the server
//     accepted -- it would pass even if the PATCH failed outright.
//   - AdminUsersPage's role <select value={user.role}> (frontend/app/
//     (shell)/admin/users/page.tsx) reflects the browser's own native
//     <select> selection the instant selectOption() runs, before React's
//     `users` state (driven by updateUser()'s PATCH resolving) re-renders.
//     toHaveValue("doctor") right after selectOption("doctor") could catch
//     that transient state and pass even though the PATCH later fails
//     (React would eventually re-render back to the prior value, but the
//     assertion may already have resolved true by then).
// A disposable diagnostic spec run during development (routing the
// branches PATCH to force a 500) confirmed this is a real, reproducible
// false-pass for an immediate no-wait assertion -- not just a theoretical
// concern. Waiting for the real PATCH response (asserting resp.ok()) and
// then reloading the page (so the follow-up check reads a *fresh* GET)
// closes the hole.
test("super_admin can create/rename a branch and create/change-role a user assigned to it", async ({ page, context }) => {
  const tenant = loadTenant();
  const { super_admin } = tenant.users;
  await injectSession(context, await getRopcTokens(super_admin.email, super_admin.password));

  const branchName = `E2E CRUD Branch ${Date.now()}`;
  const renamedBranchName = `${branchName} (renamed)`;

  await page.goto("/admin/branches");
  await page.getByPlaceholder("Name der Filiale").fill(branchName);
  const [createResponse] = await Promise.all([
    page.waitForResponse(
      (resp) => resp.request().method() === "POST" && /\/api\/admin\/branches$/.test(resp.url())
    ),
    page.getByRole("button", { name: "Anlegen" }).click(),
  ]);
  expect(createResponse.ok(), `branch create POST failed: ${createResponse.status()}`).toBe(true);

  const renameInput = await locateBranchRenameInputByValue(page, branchName);
  const [renamePatchResponse] = await Promise.all([
    page.waitForResponse(
      (resp) => resp.request().method() === "PATCH" && /\/api\/admin\/branches\/[^/]+$/.test(resp.url())
    ),
    (async () => {
      await renameInput.fill(renamedBranchName);
      await renameInput.blur();
    })(),
  ]);
  expect(renamePatchResponse.ok(), `branch rename PATCH failed: ${renamePatchResponse.status()}`).toBe(true);

  // Reload so the follow-up check reads the renamed branch back from a
  // fresh GET /api/admin/branches -- proof the rename is durable
  // server-side, not just what the controlled <input>'s local onChange
  // state showed a moment ago.
  await page.reload();
  const renamedInputAfterReload = await locateBranchRenameInputByValue(page, renamedBranchName);
  await expect(renamedInputAfterReload).toHaveValue(renamedBranchName);

  const userEmail = `e2e-crud-user-${Date.now()}@example.test`;
  await page.goto("/admin/users");
  await page.getByLabel("E-Mail").fill(userEmail);
  await page.getByLabel("Vorname").fill("CRUD");
  await page.getByLabel("Nachname").fill("Test");
  await page.locator("form").getByLabel("Rolle").selectOption("staff");
  // Depends on the branch rename above having landed server-side already
  // (confirmed via the awaited PATCH response, not local state) --
  // /admin/users' branch dropdown is populated by its own fresh
  // listBranches() call on page load, so selecting by the *renamed* label
  // proves the branch created above is the one actually assigned to the
  // new user.
  await page.locator("form").getByLabel("Filiale").selectOption({ label: renamedBranchName });
  await page.getByRole("button", { name: "Benutzer anlegen" }).click();

  // Unlike the two checks above, this one isn't racy: the row for this
  // *brand-new* user cannot exist in the DOM at all until createUser()'s
  // POST resolves and setUsers() adds it to React state -- there is no
  // optimistic/local-only rendering path that could make it appear early.
  await expect(page.getByText(userEmail)).toBeVisible();

  const userRow = page.locator("tr", { hasText: userEmail });
  const roleSelect = userRow.locator("select").first();
  const [rolePatchResponse] = await Promise.all([
    page.waitForResponse(
      (resp) => resp.request().method() === "PATCH" && /\/api\/admin\/users\/[^/]+$/.test(resp.url())
    ),
    roleSelect.selectOption("doctor"),
  ]);
  expect(rolePatchResponse.ok(), `user role-change PATCH failed: ${rolePatchResponse.status()}`).toBe(true);

  // Same reasoning as the branch rename: reload so the follow-up check
  // reads the role back from a fresh GET /api/admin/users, not the native
  // <select>'s instantaneous local selection.
  await page.reload();
  const userRowAfterReload = page.locator("tr", { hasText: userEmail });
  await expect(userRowAfterReload.locator("select").first()).toHaveValue("doctor");
});
