import { test, expect, type Page } from "@playwright/test";
async function connect(page: Page, key: string) {
  await page.goto("/");
  await page.getByLabel("Business credential").fill(key);
  await page.getByRole("button", { name: "Connect securely" }).click();
  await expect(page.getByRole("button", { name: "Disconnect" })).toBeVisible();
}
async function choose(page: Page, label: string, value: string) {
  await page.getByRole("combobox", { name: label, exact: true }).click();
  await page.getByRole("option", { name: new RegExp(value) }).click();
}
test("isolated seller and buyer sessions complete the exchange with click-only bookkeeping", async ({
  browser,
  request,
}, testInfo) => {
  test.setTimeout(60000);
  const { keys } = await (await request.post("/__test/reset")).json();
  const sellerContext = await browser.newContext({
    viewport: testInfo.project.use.viewport,
    isMobile: testInfo.project.use.isMobile,
    hasTouch: testInfo.project.use.hasTouch,
  });
  const buyerContext = await browser.newContext({
    viewport: testInfo.project.use.viewport,
    isMobile: testInfo.project.use.isMobile,
    hasTouch: testInfo.project.use.hasTouch,
  });
  const operatorContext = await browser.newContext({
    viewport: testInfo.project.use.viewport,
    isMobile: testInfo.project.use.isMobile,
    hasTouch: testInfo.project.use.hasTouch,
  });
  try {
    const seller = await sellerContext.newPage();
    const buyer = await buyerContext.newPage();
    const operator = await operatorContext.newPage();
    await connect(seller, keys.seller_only);
    await connect(buyer, keys.buyer_only);
    await connect(operator, keys.operator);
    await expect(
      seller.getByRole("button", { name: "Buyer", exact: true }),
    ).toHaveCount(0);
    await seller.screenshot({
      path: testInfo.outputPath("seller-workspace.png"),
      fullPage: true,
    });
    const sent = seller.waitForResponse(
      (r) =>
        r.url().endsWith("/v1/seller/source-documents/1/send") &&
        r.request().method() === "POST",
    );
    await seller
      .getByRole("button", { name: "Send invoice", exact: true })
      .click();
    expect((await sent).status()).toBe(202);
    await request.post("/__test/drain");
    await buyer.getByRole("button", { name: "Refresh", exact: true }).click();
    await buyer
      .getByRole("button", { name: "BROWSER-001", exact: true })
      .click();
    await choose(buyer, "Supplier", "Demo supplier");
    await choose(buyer, "Accounting item · line 1", "Network cable");
    await choose(buyer, "Purchase account · line 1", "Goods inventory");
    await choose(buyer, "Tax code · 15%", "Demo tax 15%");
    await buyer.getByRole("button", { name: "Review proposed entry" }).click();
    await expect(
      buyer.locator(".entry-preview").getByText("USD 230.00", { exact: true }),
    ).toBeVisible();
    await buyer.screenshot({
      path: testInfo.outputPath("buyer-review.png"),
      fullPage: true,
    });
    await buyer.getByRole("button", { name: "Approve & queue bill" }).click();
    await expect(buyer.getByRole("dialog")).toHaveCount(0);
    await request.post("/__test/drain");
    await seller.getByRole("button", { name: "Refresh", exact: true }).click();
    await expect(
      seller
        .getByRole("cell", { name: "Draft bill created", exact: true })
        .first(),
    ).toBeVisible();
    await operator
      .getByRole("button", { name: "Refresh", exact: true })
      .click();
    await expect(
      operator.getByRole("button", { name: "BROWSER-001", exact: true }),
    ).toBeVisible();
    await operator.screenshot({
      path: testInfo.outputPath("cbin-operations.png"),
      fullPage: true,
    });
    const docs = await (
      await request.get("/v1/documents", {
        headers: { Authorization: `Bearer ${keys.buyer_only}` },
      })
    ).json();
    expect(docs.items).toHaveLength(1);
    expect(docs.items[0].posted_reference).toMatch(/^sandbox:/);
    const before = docs.items[0].id;
    const duplicate = await request.post("/v1/seller/source-documents/1/send", {
      headers: { Authorization: `Bearer ${keys.seller_only}` },
    });
    expect((await duplicate.json()).id).toBe(before);
  } finally {
    await Promise.all([
      sellerContext.close(),
      buyerContext.close(),
      operatorContext.close(),
    ]);
  }
});
