// Expects a disposable local fixture/session; never uses a production site.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");

(async () => {
  const fixture = JSON.parse(
    fs.readFileSync(
      process.env.ADAPTER_UI_FIXTURE || "/tmp/frappe-adapter-ui-qa.json",
      "utf8",
    ),
  );
  const base = process.env.ADAPTER_UI_BASE || "http://127.0.0.1:8094";
  assert.equal(
    new URL(base).hostname,
    "127.0.0.1",
    "Use a dedicated local hostname proxy",
  );
  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1050 },
    });
    await context.addCookies([{ name: "sid", value: fixture.sid, url: base }]);
    const page = await context.newPage();
    const errors = [];
    page.on("pageerror", (error) => errors.push(error.message));
    await page.goto(`${base}/ocr#run=${fixture.run}`, {
      waitUntil: "domcontentloaded",
    });
    await page.locator(".review-desk").waitFor({ timeout: 45000 });
    await page.getByRole("tab", { name: "Invoice", exact: true }).click();
    await page
      .getByText("Check an existing Purchase Invoice", { exact: true })
      .click();
    assert.equal(
      await page.getByLabel("Purchase Invoice", { exact: true }).count(),
      1,
    );
    await page.getByRole("tab", { name: "Items", exact: true }).click();
    if (!process.env.ADAPTER_UI_SKIP_CREATE) {
      await page
        .getByRole("button", { name: "Create item", exact: true })
        .click();
      await page
        .getByLabel("Item code", { exact: true })
        .fill(fixture.item_code);
      await page
        .getByLabel("Item name", { exact: true })
        .fill("Reviewed item name from adapter UI");
      await page
        .getByLabel("Item group", { exact: true })
        .fill(fixture.item_group);
      await page
        .getByRole("option")
        .filter({ hasText: fixture.item_group })
        .first()
        .click();
      const response = page.waitForResponse((res) =>
        res.url().endsWith("frappe_tools.api.adapter_ui.run_action"),
      );
      await page
        .getByRole("button", { name: "Create item and use it", exact: true })
        .click();
      const result = await response;
      assert.equal(result.status(), 200, JSON.stringify(await result.json()));
      await page
        .getByRole("dialog")
        .waitFor({ state: "hidden", timeout: 20000 });
    }
    await page
      .locator(".adapter-record-preview")
      .getByText("Reviewed item name from adapter UI", { exact: true })
      .waitFor();
    await page
      .locator(".adapter-record-preview")
      .getByText(fixture.item_code, { exact: true })
      .waitFor();
    const matchInput = page.getByLabel("Invoice Item", { exact: true });
    await matchInput.fill("Unsaved item selection");
    await page.getByRole("tab", { name: "Invoice", exact: true }).click();
    await page.getByRole("tab", { name: "Items", exact: true }).click();
    assert.equal(await matchInput.inputValue(), "Unsaved item selection");
    await matchInput.fill(fixture.item_code);
    await page
      .getByRole("option")
      .filter({ hasText: fixture.item_code })
      .first()
      .click();
    await page.screenshot({
      path: "/tmp/adapter-review-desktop.png",
      fullPage: true,
    });
    await page.reload({ waitUntil: "domcontentloaded" });
    await page.getByRole("tab", { name: "Items", exact: true }).click();
    await page
      .locator(".adapter-record-preview")
      .getByText("Reviewed item name from adapter UI", { exact: true })
      .waitFor();
    await page.setViewportSize({ width: 390, height: 844 });
    await page.screenshot({
      path: "/tmp/adapter-review-mobile.png",
      fullPage: true,
    });
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > window.innerWidth,
      ),
      false,
    );
    assert.deepEqual(errors, []);
    console.log(
      JSON.stringify({
        ok: true,
        checks: [
          "custom invoice lookup",
          "adapter item form",
          "real Item creation",
          "record preview",
          "saved mapping after reload",
          "mobile width",
          "no browser errors",
        ],
      }),
    );
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
