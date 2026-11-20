import { expect, test, type Page } from "@playwright/test";

async function login(page: Page, email: string) {
  await page.goto("/login");
  await page.getByLabel("E-mail").fill(email);
  await page.getByLabel("Password").fill("demo-password");
  await page.getByRole("button", { name: "Sign in" }).click();
  await expect(page.getByTestId("me")).toBeVisible();
}

test("respondent connects a source, collects facts, uploads a file and submits; owner approves", async ({ page }) => {
  await login(page, "alice@example.test");

  // connect the read-only asset registry with Alice's own key
  await page.getByRole("link", { name: "My connections" }).click();
  const form = page.getByTestId("connect-Asset Registry");
  await form.getByLabel("API key").fill("key-alice");
  await form.getByRole("button", { name: "Connect" }).click();
  await expect(page.getByTestId("connections")).toContainText("Asset Registry");
  await expect(page.getByTestId("connections")).toContainText("active");

  // open the CRM job
  await page.getByRole("link", { name: "My jobs" }).click();
  await page.getByRole("link", { name: "CRM" }).click();
  await expect(page.getByTestId("messages")).toContainText("collecting information");

  // ask the agent to look it up in the connected source
  await page.getByLabel(/Type a message/).fill("please look up crm in the registry");
  await page.getByRole("button", { name: "Send" }).click();
  const proposals = page.getByTestId("proposals");
  await expect(proposals).toContainText("number_of_users");
  await expect(proposals).toContainText("Asset Registry / lookup_application");
  for (const btn of await proposals.getByRole("button", { name: "Confirm" }).all()) await btn.click();
  await expect(page.getByTestId("draft")).toContainText("1200");

  // state facts in the chat
  await page.getByLabel(/Type a message/).fill("name: CRM\nbusiness_owner: Sales\ncriticality: low");
  await page.getByRole("button", { name: "Send" }).click();
  await expect(proposals).toContainText("business_owner");
  while (await proposals.getByRole("button", { name: "Confirm" }).count()) {
    await proposals.getByRole("button", { name: "Confirm" }).first().click();
    await page.waitForTimeout(300);
  }
  await expect(page.getByTestId("draft")).toContainText("Sales");

  // upload a private file
  await page.getByTestId("file-input").setInputFiles({ name: "notes.md", mimeType: "text/markdown", buffer: Buffer.from("# Notes\nsome context\n") });
  await expect(page.getByTestId("attachments")).toContainText("notes.md");
  await expect(page.getByTestId("attachments")).toContainText("private");

  // submit
  await page.getByRole("button", { name: "Submit" }).click();
  await expect(page.getByText(/Submitted \(v1\)/)).toBeVisible();
  await expect(page.getByTestId("messages")).toContainText("passed validation");

  // cycle owner approves; the private file stays invisible to them
  await page.getByRole("button", { name: "Log out" }).click();
  await login(page, "owner@example.test");
  await page.getByRole("link", { name: "Cycles" }).click();
  await page.getByRole("link", { name: "app-inventory-2026-Q4" }).click();
  await expect(page.getByTestId("cycle-status")).toContainText("submitted: 1");
  await page.getByRole("link", { name: "CRM" }).click();
  await page.getByTestId("approve").click();
  await expect(page.getByText("approved").first()).toBeVisible();
  await expect(page.getByTestId("factsheet")).toContainText("1200");
  await page.getByRole("tab", { name: "Shared files" }).click();
  await expect(page.getByText("notes.md")).toHaveCount(0);

  // version diff with Monaco
  await page.getByRole("tab", { name: "Compare versions" }).click();
  await page.getByTestId("diff-from").selectOption("1");
  await expect(page.locator(".monaco-diff-editor")).toBeVisible();

  // localisation
  await page.getByLabel("Language").selectOption("de");
  await expect(page.getByRole("link", { name: "Meine Aufträge" })).toBeVisible();
});

test("respondent connects an OAuth source with read & write access and confirms a write", async ({ page }) => {
  await login(page, "bob@example.test");
  await page.getByRole("link", { name: "My connections" }).click();
  const form = page.getByTestId("connect-Tickets");
  await form.getByLabel("Access mode").selectOption("read_write");
  await form.getByRole("button", { name: "Connect" }).click();
  // fixture authorization server redirects back through the OAuth callback
  await expect(page).toHaveURL(/\/connections\?connected=1/);
  await expect(page.getByTestId("connections")).toContainText("Tickets");
  await expect(page.getByTestId("connections")).toContainText("active");

  await page.getByRole("link", { name: "My jobs" }).click();
  await page.getByRole("link", { name: "ERP" }).click();
  await page.getByLabel(/Type a message/).fill("write: please update source with ERP owner Finance");
  await page.getByRole("button", { name: "Send" }).click();
  const confirmations = page.getByTestId("write-confirmations");
  await expect(confirmations).toContainText("Tickets (create_ticket)");
  await confirmations.getByRole("button", { name: "Allow write" }).click();
  await expect(page.getByTestId("messages")).toContainText("Thanks");
  await expect(confirmations).toHaveCount(0);
});
