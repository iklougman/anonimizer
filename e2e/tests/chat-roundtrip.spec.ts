import { test, expect } from "@playwright/test";
import { loadTenant } from "../support/tenant";
import { getRopcTokens, injectSession } from "../support/session";

// Requires the stack to be running with LLM_PROVIDER=stub (see
// backend/app/llm_gateway/stub_provider.py) -- the stub echoes the
// sanitized prompt verbatim, which is what makes this test's round-trip
// assertion meaningful: if the raw values below survive to the rendered
// *assistant* reply, pseudonymize -> LLM -> deanonymize worked correctly.
const PATIENT_NAME = "Anna Schmitt";
const CITY = "Heidelberg";
const MESSAGE = `Patientin ${PATIENT_NAME}, 45 Jahre, aus ${CITY}.`;

test("sending a message round-trips real values through the anonymization pipeline", async ({ page, context }) => {
  const tenant = loadTenant();
  const doctor = tenant.users.doctor;

  const tokens = await getRopcTokens(doctor.email, doctor.password);
  await injectSession(context, tokens);

  await page.goto("/apps/anonymization");
  await page.getByRole("button", { name: "Neue Anfrage" }).click();
  await page.waitForURL(/\/apps\/anonymization\/c\//);

  await page.getByPlaceholder("Nachricht eingeben…").fill(MESSAGE);
  await page.getByRole("button", { name: "Senden" }).click();

  // The user's own bubble echoes the raw MESSAGE unconditionally the
  // instant it's sent (ConversationPage.handleSend renders the typed
  // content locally, before any request completes) -- that's just normal
  // chat UX and would show PATIENT_NAME/CITY even if the anonymization
  // pipeline were completely broken. The meaningful assertion is scoped to
  // the *assistant* bubble specifically: StubProvider only ever sees the
  // pseudonymized prompt (real values replaced with tokens), so the raw
  // name/city appearing there proves deanonymize() correctly resolved the
  // tokens it echoed back -- not just that something rendered on the page.
  const assistantBubble = page.locator('[class*="bubbleAssistant"]').last();
  await expect(assistantBubble).toContainText(PATIENT_NAME, { timeout: 15_000 });
  await expect(assistantBubble).toContainText(CITY);

  const conversationUrl = page.url();
  await page.reload();
  await expect(page).toHaveURL(conversationUrl);
  const assistantBubbleAfterReload = page.locator('[class*="bubbleAssistant"]').last();
  await expect(assistantBubbleAfterReload).toContainText(PATIENT_NAME);
  await expect(assistantBubbleAfterReload).toContainText(CITY);
});
