import { test, expect, type Page } from "@playwright/test";
async function select(page: Page, label: string, name: string) {
  const field = page.getByRole("combobox", { name: label, exact: true });
  await field.click();
  await page.getByRole("option", { name: new RegExp(name) }).click();
}
async function connect(page: Page, key: string) {
  await page.goto("/");
  await page.getByLabel("Business credential").fill(key);
  await page.getByRole("button", { name: "Connect securely" }).click();
}
async function seed(request: any) {
  const r = await request.post("/__test/reset");
  expect(r.ok()).toBeTruthy();
  return r.json();
}
async function submit(
  request: any,
  keys: any,
  invoice: any,
  reference: string,
) {
  const r = await request.post("/v1/documents", {
    headers: {
      Authorization: `Bearer ${keys.seller}`,
      "Idempotency-Key": reference,
    },
    data: { ...invoice, external_reference: reference },
  });
  expect(r.status()).toBe(202);
  await request.post("/__test/drain");
  return (await r.json()).id;
}

test("buyer records purchase, previews entry, posts once and reuses approved mappings", async ({
  page,
  request,
}, testInfo) => {
  const errors: string[] = [];
  page.on("pageerror", (e) => errors.push(e.message));
  const { keys, invoice } = await seed(request);
  const id = await submit(request, keys, invoice, "BROWSER-001");
  await connect(page, keys.buyer);
  await page.getByRole("button", { name: "BROWSER-001" }).click();
  await expect(
    page.getByRole("heading", { name: "Record this purchase" }),
  ).toBeVisible();
  await expect(
    page.getByRole("button", { name: "Review proposed entry" }),
  ).toBeDisabled();
  await select(page, "Supplier", "Demo supplier");
  await select(page, "Accounting item · line 1", "Network cable");
  await select(page, "Purchase account · line 1", "Office supplies");
  await select(page, "Tax code · 15%", "Demo tax 15%");
  await select(page, "Purchase order (optional)", "PO-001");
  await page.getByRole("button", { name: "Review proposed entry" }).click();
  await expect(
    page.getByRole("heading", { name: "Proposed accounting entry" }),
  ).toBeVisible();
  await expect(
    page.locator(".entry-preview").getByText("USD 230.00", { exact: true }),
  ).toBeVisible();
  await expect(page.getByText(/Purchase order header matched/)).toBeVisible();
  // Changing an allocation invalidates the reviewed preview before approval.
  await select(page, "Purchase account · line 1", "Equipment");
  await expect(
    page.getByRole("button", { name: "Approve & queue bill" }),
  ).toHaveCount(0);
  await page.getByRole("button", { name: "Review proposed entry" }).click();
  await expect(
    page.locator(".entry-preview").getByText("Equipment", { exact: true }),
  ).toBeVisible();
  await page.screenshot({
    path: testInfo.outputPath("bookkeeping-review.png"),
    fullPage: true,
  });
  await page.getByRole("button", { name: "Approve & queue bill" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await request.post("/__test/drain");
  const decision = await (await request.get(`/__test/decision/${id}`)).json();
  expect(decision.status).toBe("posted");
  expect(decision.posted_reference).toBe(`sandbox:${id}`);
  expect(decision.mapping.line_allocations[0].account_reference).toBe(
    "ASSET-EQUIPMENT",
  );
  await submit(request, keys, invoice, "BROWSER-002");
  await page.getByRole("button", { name: "Refresh", exact: true }).click();
  await page.getByRole("button", { name: "BROWSER-002" }).click();
  await expect(
    page.getByText(/Previous approved choices applied/),
  ).toBeVisible();
  await expect(
    page.getByRole("combobox", { name: "Supplier", exact: true }),
  ).toHaveValue("Demo supplier");
  await expect(
    page.getByRole("combobox", { name: "Purchase account · line 1" }),
  ).toHaveValue("Equipment");
  await expect(
    page.getByRole("combobox", { name: "Purchase order (optional)" }),
  ).toHaveValue("");
  await expect(
    page.getByRole("button", { name: "Approve & queue bill" }),
  ).toHaveCount(0);
  expect(
    await page.evaluate(() => localStorage.length + sessionStorage.length),
  ).toBe(0);
  expect(
    await page.evaluate(
      () => document.documentElement.scrollWidth > window.innerWidth,
    ),
  ).toBe(false);
  await page.keyboard.press("Escape");
  await expect(page.getByRole("dialog")).toHaveCount(0);
  expect(errors).toEqual([]);
});

test("buyer rejects a document without creating a posting decision", async ({
  page,
  request,
}) => {
  const { keys, invoice } = await seed(request);
  const id = await submit(request, keys, invoice, "REJECT-001");
  await connect(page, keys.buyer);
  await page.getByRole("button", { name: "REJECT-001" }).click();
  await page.getByLabel("Reason for rejection").fill("Incorrect quantities");
  await page.getByRole("button", { name: "Reject invoice" }).click();
  await expect(page.getByRole("dialog")).toHaveCount(0);
  await request.post("/__test/drain");
  const result = await (await request.get(`/__test/decision/${id}`)).json();
  expect(result.status).toBe("rejected_by_buyer");
  expect(result.posted_reference).toBeNull();
});

test("missing accounting connection shows an error and does not invent references", async ({
  page,
  request,
}) => {
  const { keys, invoice } = await seed(request);
  await submit(request, keys, invoice, "UNAVAILABLE-001");
  await page.route("**/v1/documents/*/bookkeeping", (route) =>
    route.fulfill({
      status: 503,
      contentType: "application/json",
      body: JSON.stringify({
        error: { message: "Accounting reference lookup unavailable" },
      }),
    }),
  );
  await connect(page, keys.buyer);
  await page.getByRole("button", { name: "UNAVAILABLE-001" }).click();
  await expect(
    page
      .getByRole("alert")
      .filter({ hasText: "Accounting reference lookup unavailable" }),
  ).toBeVisible();
  await expect(page.getByRole("dialog").getByRole("combobox")).toHaveCount(0);
  await expect(
    page.getByRole("button", { name: "Approve & queue bill" }),
  ).toHaveCount(0);
});
