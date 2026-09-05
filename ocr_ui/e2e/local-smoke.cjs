const { chromium } = require(
  process.env.PLAYWRIGHT_PACKAGE || "playwright-core",
);
const fs = require("fs");
const path = require("path");

const password = process.env.FRAPPE_TEST_PASSWORD;
if (!password) throw new Error("FRAPPE_TEST_PASSWORD is required");
const lrRun = process.env.LR_RUN;
const piRun = process.env.PI_RUN;
if (!lrRun || !piRun) throw new Error("LR_RUN and PI_RUN are required");

const root = path.resolve(__dirname, "..", "..");
const proofDir = path.join(root, "docs", "proofs");
const executablePath =
  process.env.PLAYWRIGHT_CHROMIUM ||
  "/run/media/sakthi/storage/libraries/playwright-browsers/chromium-1243/chrome-linux64/chrome";
fs.mkdirSync(proofDir, { recursive: true });

function check(condition, message) {
  if (!condition) throw new Error(message);
}

(async () => {
  const browser = await chromium.launch({
    headless: true,
    executablePath,
    args: ["--host-resolver-rules=MAP *.site 127.0.0.1"],
  });
  const context = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const page = await context.newPage();
  const pageErrors = [];
  const failedResponses = [];
  page.on("pageerror", (error) => pageErrors.push(error.message));
  page.on("response", (response) => {
    if (response.status() >= 400 && response.url().includes("/ocr")) {
      failedResponses.push({ status: response.status(), url: response.url() });
    }
  });
  try {
    await page.goto("http://erp-prod.site:8000/login", {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    await page.locator("#login_email").fill("Administrator");
    await page.locator("#login_password").fill(password);
    await page.locator("button.btn-login:visible").first().click();
    await page.waitForURL((url) => !url.pathname.includes("login"), {
      timeout: 45000,
    });

    await page.goto("http://erp-prod.site:8000/ocr", {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    await page.getByText("My documents", { exact: true }).waitFor();
    check(
      (await page.getByText("Recent", { exact: true }).count()) === 0,
      "Recent list must not be visible",
    );
    check(
      (await page
        .getByText(
          /Frappe permissions|permitted operations|Permission checked/i,
        )
        .count()) === 0,
      "Internal permission wording must not be visible",
    );
    await page.getByPlaceholder("Search document or created draft").waitFor();
    await page.getByText(/Page 1 of/).waitFor();
    await page.screenshot({
      path: path.join(proofDir, "document-queue.png"),
      fullPage: true,
    });
    await page
      .getByRole("main")
      .getByRole("button", { name: "Upload document", exact: true })
      .click();
    await page.getByRole("heading", { name: "Create from scan" }).waitFor();
    await page.getByText("Phone scanner", { exact: true }).waitFor();
    await page.getByText(/no API key or secret/i).waitFor();
    const scannerPin = (
      await page.locator(".live-scanner-pin").innerText()
    ).trim();
    check(
      /^\d{4}$/.test(scannerPin),
      "The browser did not create a live scanner PIN.",
    );
    await page.getByRole("radio", { name: /LR Processing Entry/ }).click();
    const layout = page.getByRole("combobox").first();
    check(
      (await layout.textContent()).includes("LR Documents"),
      "LR Documents was not selected for LR Processing Entry.",
    );
    await page.getByRole("radio", { name: /Purchase Invoice/ }).click();
    await layout.click();
    const options = await page.getByRole("option").allInnerTexts();
    check(
      options.includes("Universal Bill Scanner"),
      "No Purchase Invoice scanner layout is available.",
    );
    await page.keyboard.press("Escape");
    await page.screenshot({
      path: path.join(proofDir, "automatic-intake.png"),
      fullPage: true,
    });

    await page.goto(`http://erp-prod.site:8000/ocr?proof=1#run=${lrRun}`, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    await page.getByText(lrRun, { exact: false }).first().waitFor();
    await page
      .getByRole("heading", { name: "LR Processing Entry", exact: true })
      .waitFor();
    await page.getByText(/Review extracted data/).waitFor();
    await page.getByText("Source", { exact: true }).first().waitFor();
    await page
      .getByRole("button", { name: /Create LR Processing Entry draft/ })
      .waitFor();
    await page.getByText("160%", { exact: true }).waitFor();
    const lrEvidenceBox = page.locator(".evidence-box").first();
    await lrEvidenceBox.waitFor();
    const lrEvidenceStyle = await lrEvidenceBox.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        backgroundColor: getComputedStyle(element).backgroundColor,
        width: rect.width,
        height: rect.height,
      };
    });
    check(
      lrEvidenceStyle.width > 0 && lrEvidenceStyle.height > 0,
      "LR evidence box has no visible dimensions.",
    );
    let updateCalls = 0;
    page.on("request", (request) => {
      if (request.url().includes("frappe_tools.api.ocr_agent.update_field"))
        updateCalls += 1;
    });
    const firstField = page.locator(".review-field").first();
    await firstField.click();
    const input = firstField.locator("input").first();
    if (await input.count()) {
      await input.focus();
      await page.keyboard.press("Tab");
    }
    await page.waitForTimeout(400);
    check(
      updateCalls === 0,
      "Field focus or blur triggered an update API call",
    );
    check(
      lrEvidenceStyle.backgroundColor === "rgba(0, 0, 0, 0)",
      `LR evidence box obscures the document: ${lrEvidenceStyle.backgroundColor}`,
    );
    await page.screenshot({
      path: path.join(proofDir, "lr-auto-review.png"),
      fullPage: true,
    });

    await page.goto(`http://erp-prod.site:8000/ocr?proof=pi#run=${piRun}`, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    await page.getByText(piRun, { exact: false }).first().waitFor();
    await page
      .getByRole("heading", { name: "Purchase Invoice", exact: true })
      .waitFor();
    await page
      .getByRole("button", { name: /Create Purchase Invoice draft/ })
      .waitFor();
    await page.getByText("160%", { exact: true }).waitFor();
    const piEvidenceBox = page.locator(".evidence-box").first();
    await piEvidenceBox.waitFor();
    const piEvidenceStyle = await piEvidenceBox.evaluate((element) => {
      const rect = element.getBoundingClientRect();
      return {
        backgroundColor: getComputedStyle(element).backgroundColor,
        width: rect.width,
        height: rect.height,
      };
    });
    check(
      piEvidenceStyle.width > 0 && piEvidenceStyle.height > 0,
      "Purchase Invoice evidence box has no visible dimensions.",
    );
    check(
      piEvidenceStyle.backgroundColor === "rgba(0, 0, 0, 0)",
      `Purchase Invoice evidence box obscures the document: ${piEvidenceStyle.backgroundColor}`,
    );
    await page.screenshot({
      path: path.join(proofDir, "purchase-invoice-auto-review.png"),
      fullPage: true,
    });

    await page.setViewportSize({ width: 390, height: 844 });
    await page.goto(`http://erp-prod.site:8000/ocr?proof=mobile#run=${lrRun}`, {
      waitUntil: "domcontentloaded",
      timeout: 45000,
    });
    const mobileSwitch = page.locator(".review-mobile-switch");
    await mobileSwitch.waitFor();
    await page.getByRole("button", { name: /Review/ }).click();
    check(
      await page.locator(".review-panel.mobile-pane-active").isVisible(),
      "Mobile review values are not visible as the default workspace.",
    );
    await page.getByRole("tab", { name: /Tables/ }).click();
    await page.getByText("Row 1", { exact: true }).waitFor();
    await page.screenshot({
      path: path.join(proofDir, "lr-mobile-review-values.png"),
      fullPage: false,
    });
    await page
      .locator(".extracted-row-heading")
      .getByRole("button", { name: /Page 1/ })
      .click();
    check(
      await page.locator(".document-viewer.mobile-pane-active").isVisible(),
      "Selecting a mobile review value did not open its source evidence.",
    );
    await page.screenshot({
      path: path.join(proofDir, "lr-mobile-source-evidence.png"),
      fullPage: false,
    });
    await mobileSwitch.getByRole("button", { name: /Review/ }).click();
    check(
      await page.getByText("Row 1", { exact: true }).isVisible(),
      "Mobile reviewer could not return from source evidence to values.",
    );
    check(
      (await page.evaluate(() => document.documentElement.scrollWidth)) === 390,
      "Mobile review introduces horizontal page overflow.",
    );

    if (pageErrors.length) {
      throw new Error(
        `Browser page errors: ${[...new Set(pageErrors)].join(" | ")}`,
      );
    }
    if (failedResponses.length) {
      throw new Error(`OCR HTTP failures: ${JSON.stringify(failedResponses)}`);
    }
    console.log(
      JSON.stringify({
        site: "erp-prod.site",
        automaticIntake: true,
        layouts: {
          lr: "LR Documents",
          purchaseInvoiceCount: options.length,
        },
        runs: [lrRun, piRun],
        reviewsVisible: true,
        mobileReviewSwitch: true,
        boundingBoxes: {
          lr: lrEvidenceStyle,
          purchaseInvoice: piEvidenceStyle,
        },
        screenshots: [
          "document-queue.png",
          "automatic-intake.png",
          "lr-auto-review.png",
          "purchase-invoice-auto-review.png",
          "lr-mobile-review-values.png",
          "lr-mobile-source-evidence.png",
        ],
      }),
    );
  } catch (error) {
    await page.screenshot({
      path: path.join(proofDir, "local-smoke-failure.png"),
      fullPage: true,
    });
    console.error(
      JSON.stringify({
        url: page.url(),
        body: (await page.locator("body").innerText()).slice(0, 4000),
        pageErrors,
      }),
    );
    throw error;
  } finally {
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
