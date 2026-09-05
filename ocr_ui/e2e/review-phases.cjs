// Disposable local review fixtures; all final document writes are explicit clicks.
const fs = require("node:fs");
const assert = require("node:assert/strict");
const { chromium } = require("playwright-core");
(async () => {
  const fixture = JSON.parse(
    fs.readFileSync(
      process.env.ADAPTER_UI_FIXTURE || "/tmp/phase-browser-fixture.json",
      "utf8",
    ),
  );
  const base = process.env.ADAPTER_UI_BASE || "http://127.0.0.1:8094";
  assert.equal(new URL(base).hostname, "127.0.0.1");
  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  const failures = [];
  try {
    const context = await browser.newContext({
      viewport: { width: 1440, height: 1050 },
    });
    await context.addCookies([{ name: "sid", value: fixture.sid, url: base }]);
    const page = await context.newPage();
    page.on("pageerror", (e) => failures.push(e.message));
    for (const [kind, steps, label] of [
      [
        "purchase_invoice",
        [
          "Supplier and company",
          "Invoice details",
          "Items",
          "Taxes and totals",
        ],
        "Create Purchase Invoice draft",
      ],
      [
        "lr",
        ["LR details", "Sales Invoice references"],
        "Save reviewed LR entry",
      ],
    ]) {
      if (process.env.ADAPTER_UI_FLOW && process.env.ADAPTER_UI_FLOW !== kind)
        continue;
      await page.goto("about:blank");
      await page.goto(`${base}/ocr#run=${fixture[kind]}`);
      await page.locator(".phase-progress").waitFor({ timeout: 45000 });
      for (const step of steps) {
        if (kind === "lr" && step === "Sales Invoice references") {
          const response = page.waitForResponse(
            (r) =>
              r.url().includes("adapter_ui.advance_phase") &&
              r.request().method() === "POST",
          );
          await page
            .getByRole("button", {
              name: "Refresh this phase's suggestions",
              exact: true,
            })
            .click();
          const result = await response;
          assert.equal(result.status(), 200, await result.text());
        }
        const response = page.waitForResponse(
          (r) =>
            r.url().includes("adapter_ui.advance_phase") &&
            r.request().method() === "POST",
        );
        await page
          .getByRole("button", {
            name: `Confirm ${step} and continue`,
            exact: true,
          })
          .click();
        const result = await response;
        assert.equal(result.status(), 200, await result.text());
      }
      await page.reload();
      await page
        .getByText(
          "All phases are confirmed. Review the checks and create the document.",
          { exact: true },
        )
        .waitFor();
      await page.setViewportSize({ width: 390, height: 844 });
      assert.equal(
        await page.evaluate(
          () => document.documentElement.scrollWidth > innerWidth,
        ),
        false,
      );
      await page.screenshot({
        path: `/tmp/${kind}-phase-review-mobile.png`,
        fullPage: true,
      });
      await page.setViewportSize({ width: 1440, height: 1050 });
      const response = page.waitForResponse(
        (r) =>
          r.url().includes("ocr_agent.create_draft") &&
          r.request().method() === "POST",
      );
      await page.getByRole("button", { name: label, exact: true }).click();
      const result = await response;
      assert.equal(result.status(), 200, await result.text());
      const body = await result.json();
      assert.ok(body.message.docname || body.message.name);
      console.log(
        `${kind}: phases resumed after reload, mobile fits, document created`,
      );
    }
    assert.deepEqual(failures, []);
  } finally {
    await browser.close();
  }
})().catch((e) => {
  console.error(e);
  process.exit(1);
});
