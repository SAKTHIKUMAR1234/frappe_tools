import assert from "node:assert/strict";
import path from "node:path";

// Run with an already authenticated, disposable Playwright page on an explicit
// local site proxy. These checks never submit a document or call an AI model.
export async function checkScannerWorkspace(
  page,
  { baseUrl, invoiceRun, lrRun, screenshotDirectory },
) {
  assert.ok(
    /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseUrl),
    "Browser checks require a local site proxy",
  );
  const requests = [];
  const errors = [];
  const onRequest = (request) => requests.push(request.url());
  const onError = (error) => errors.push(error.message);
  page.on("request", onRequest);
  page.on("pageerror", onError);
  const proofs = [];
  const screenshot = async (name) => {
    for (const source of await page.locator(".document-sheet img").all()) {
      await source.evaluate(async (image) => {
        await image.decode();
        if (!image.naturalWidth)
          throw new Error("Source document failed to load");
      });
    }
    const filename = path.join(screenshotDirectory, `${name}.png`);
    await page.screenshot({ path: filename, animations: "disabled" });
    proofs.push(filename);
  };
  const noOverflow = async () => {
    assert.equal(
      await page.evaluate(
        () => document.documentElement.scrollWidth > innerWidth,
      ),
      false,
      "No horizontal page overflow",
    );
  };
  try {
    await page.setViewportSize({ width: 1536, height: 1024 });
    await page.goto(
      `${baseUrl}/ocr?ui_check=invoice#run=${encodeURIComponent(invoiceRun)}`,
      { waitUntil: "domcontentloaded" },
    );
    await page.getByRole("tab", { name: "Parties", exact: true }).waitFor();
    assert.equal(
      await page.getByRole("tab", { name: "Invoice", exact: true }).count(),
      1,
    );
    await noOverflow();
    assert.equal(
      await page.locator(".create-document-action").isVisible(),
      true,
    );
    await screenshot("scanner-review-desktop-20260905");

    for (const width of [1024, 390, 320]) {
      await page.setViewportSize({ width, height: 844 });
      await noOverflow();
    }
    await page.setViewportSize({ width: 390, height: 844 });
    assert.ok(
      (
        await page
          .getByRole("navigation", { name: "Review workspace" })
          .getByRole("button")
          .first()
          .innerText()
      ).includes("Review"),
      "Review is the first mobile workspace",
    );
    await page
      .getByRole("button", { name: /Resolve .* required checks/ })
      .click();
    await page
      .locator(".issue-card")
      .filter({ hasText: "Confirm Supplier before" })
      .click();
    assert.equal(
      await page.locator(".review-panel.mobile-pane-active").isVisible(),
      true,
      "A required check opens the editor, not the document pane",
    );
    assert.equal(
      await page.evaluate(() => document.activeElement?.id),
      "field-supplier",
      "A check focuses the exact input",
    );
    await page.locator(".action-outcome summary").click();
    assert.equal(
      await page.locator(".action-outcome").getAttribute("open"),
      "",
    );
    await page.locator(".action-outcome summary").click();
    assert.equal(
      await page.locator(".action-outcome").getAttribute("open"),
      null,
    );
    const field = page.getByRole("textbox", {
      name: "Supplier GSTIN",
      exact: true,
    });
    const original = await field.inputValue();
    await field.fill("UNSAVED-LOCAL-UI-CHECK");
    assert.equal(
      await page.locator(".review-panel.mobile-pane-active").isVisible(),
      true,
      "Typing must not switch panes",
    );
    await page.getByRole("tab", { name: "Invoice", exact: true }).click();
    await page.getByRole("tab", { name: "Parties", exact: true }).click();
    assert.equal(
      await field.inputValue(),
      "UNSAVED-LOCAL-UI-CHECK",
      "Tab changes retain unsaved values",
    );
    await page.getByRole("button", { name: "New scan", exact: true }).click();
    const leave = page.getByRole("dialog", { name: "Leave without saving?" });
    await leave.waitFor();
    await page.keyboard.press("Escape");
    await leave.waitFor({ state: "hidden" });
    assert.equal(
      await field.inputValue(),
      "UNSAVED-LOCAL-UI-CHECK",
      "Dismissing leave dialog preserves edits",
    );
    await page.getByRole("button", { name: "New scan", exact: true }).click();
    await leave.getByRole("button", { name: "Keep working" }).click();
    assert.equal(await field.inputValue(), "UNSAVED-LOCAL-UI-CHECK");
    await page
      .getByRole("button", { name: "View source for Supplier", exact: true })
      .click();
    assert.equal(
      await page.locator(".document-viewer.mobile-pane-active").isVisible(),
      true,
    );
    await page
      .getByRole("navigation", { name: "Review workspace" })
      .getByRole("button", { name: /Review/ })
      .click();
    await field.fill(original);
    await page.locator(".review-panel .review-scroll").evaluate((element) => {
      element.scrollTop = 0;
    });
    await screenshot("scanner-review-mobile-20260905");

    await page.getByRole("tab", { name: "Items", exact: true }).click();
    const firstCell = page
      .locator('[role="tabpanel"]:visible .extracted-cell input')
      .first();
    if (await firstCell.count()) {
      const value = await firstCell.inputValue();
      await firstCell.fill(value);
      assert.equal(
        await page.locator(".review-panel.mobile-pane-active").isVisible(),
        true,
        "Table input must not open evidence",
      );
    }

    await page.setViewportSize({ width: 1536, height: 1024 });
    await page.goto(
      `${baseUrl}/ocr?ui_check=lr#run=${encodeURIComponent(lrRun)}`,
      { waitUntil: "domcontentloaded" },
    );
    await page.getByRole("tab", { name: "LR details", exact: true }).waitFor();
    assert.equal(
      await page.getByRole("tab", { name: "Parties", exact: true }).count(),
      0,
      "LR must use its own workflow",
    );
    assert.equal(
      await page
        .getByRole("button", { name: "Save reviewed LR entry", exact: true })
        .filter({ visible: true })
        .count(),
      1,
    );
    await page
      .getByRole("tab", { name: "Sales Invoices", exact: true })
      .click();
    await screenshot("scanner-lr-review-20260905");

    requests.length = 0;
    await page
      .getByRole("button", { name: "New scan", exact: true })
      .filter({ visible: true })
      .click();
    await page.getByRole("heading", { name: "Add a document" }).waitFor();
    await page.getByRole("radio", { name: /Purchase Invoice/ }).click();
    assert.equal(
      requests.some((request) => request.includes("doc_scanner.")),
      false,
      "Opening intake must not start phone signaling",
    );
    await noOverflow();
    await screenshot("scanner-intake-desktop-20260905");
    await page.locator(".phone-source summary").click();
    await page
      .getByRole("button", { name: "Connect phone", exact: true })
      .click();
    await page.getByText("Waiting for phone", { exact: true }).waitFor();
    await page.getByRole("button", { name: "Disconnect", exact: true }).click();
    await page
      .getByRole("button", { name: "Connect phone", exact: true })
      .waitFor();
    assert.equal(await page.locator(".live-scanner-pin").count(), 0);
    assert.equal(errors.length, 0, errors.join("\n"));
    return {
      passed: true,
      proofs,
      checks: [
        "adapter sections",
        "390/320/1024/1536px overflow",
        "review-first mobile navigation",
        "required check focuses correct input",
        "outcome disclosure opens and closes",
        "mobile field and table editing",
        "draft preservation",
        "explicit source navigation",
        "LR-specific action",
        "no idle phone signaling",
        "connect/disconnect",
        "no JavaScript exceptions",
      ],
    };
  } finally {
    page.off("request", onRequest);
    page.off("pageerror", onError);
  }
}

// The caller must create a disposable Review-state Purchase Invoice extraction
// and remove it after this check. No business invoice is created or submitted.
export async function checkSavedReview(page, { baseUrl, extraction }) {
  assert.ok(
    /^https?:\/\/(127\.0\.0\.1|localhost)(:\d+)?$/.test(baseUrl),
    "Save checks require a local site proxy",
  );
  const value = `UI-QA-${extraction}`;
  await page.setViewportSize({ width: 390, height: 844 });
  await page.goto(
    `${baseUrl}/ocr?ui_check=save#run=${encodeURIComponent(extraction)}`,
    { waitUntil: "domcontentloaded" },
  );
  await page.getByRole("tab", { name: "Invoice", exact: true }).click();
  const input = page.locator("#field-bill_no");
  await input.fill(value);
  const row = page.locator(".review-field").filter({ has: input });
  const [response] = await Promise.all([
    page.waitForResponse(
      (response) =>
        response.url().includes("ocr_agent.update_field") &&
        response.request().method() === "POST",
    ),
    row.getByRole("button", { name: /^Save / }).click(),
  ]);
  assert.equal(
    response.status(),
    200,
    "Saving the field succeeds on the migrated backend",
  );
  const result = await response.json();
  assert.equal(
    result.message?.fields?.find((field) => field.fieldname === "bill_no")
      ?.value,
    value,
  );
  await page.reload({ waitUntil: "domcontentloaded" });
  await page.getByRole("tab", { name: "Invoice", exact: true }).click();
  assert.equal(
    await input.inputValue(),
    value,
    "Saved edit survives a full reload",
  );
  assert.equal(
    await page.locator(".create-document-action").isEnabled(),
    false,
    "Unresolved checks still prevent target creation",
  );
  return {
    passed: true,
    extraction,
    checks: [
      "save through real backend API",
      "persisted value after browser reload",
      "remaining checks keep creation blocked",
    ],
  };
}

export async function checkCapturePreview(page, { sampleFile }) {
  await page.setViewportSize({ width: 1536, height: 1024 });
  await page
    .getByRole("button", { name: "New scan", exact: true })
    .filter({ visible: true })
    .click();
  await page.getByRole("heading", { name: "Add a document" }).waitFor();
  await page.getByRole("radio", { name: /Purchase Invoice/ }).click();
  await page.locator('input[type="file"][multiple]').setInputFiles(sampleFile);
  const thumbnail = page.locator(".queued-page img");
  await thumbnail.waitFor();
  await thumbnail.evaluate((image) => image.decode());
  assert.equal(await page.locator(".queued-page").count(), 1);
  await page.getByRole("button", { name: "Rotate page", exact: true }).click();
  assert.equal(
    await thumbnail.evaluate((image) => image.style.transform),
    "rotate(90deg)",
  );
  await thumbnail.click();
  const dialog = page.getByRole("dialog", {
    name: "Page preview",
    exact: true,
  });
  await dialog.waitFor();
  await dialog.locator("img").evaluate((image) => image.decode());
  await dialog.getByRole("button", { name: "Close", exact: true }).click();
  await dialog.waitFor({ state: "hidden" });
  await thumbnail.click();
  await dialog.waitFor();
  await page.keyboard.press("Escape");
  await dialog.waitFor({ state: "hidden" });
  await page.getByRole("button", { name: "Remove page", exact: true }).click();
  assert.equal(await page.locator(".queued-page").count(), 0);
  await page.getByRole("heading", { name: "Drop your scans here" }).waitFor();
  return {
    passed: true,
    checks: [
      "local file selection",
      "loaded page preview",
      "rotation",
      "preview close button and Escape",
      "remove staged page",
    ],
  };
}
