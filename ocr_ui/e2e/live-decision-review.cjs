const { chromium } = require("playwright-core");

(async () => {
  const base = process.env.OCR_E2E_BASE || "http://erp-prod.site:8000";
  const run = process.env.OCR_E2E_RUN || "DOCEXT-00006";
  const output =
    process.env.OCR_E2E_SCREENSHOT || "/tmp/document-automation-decision.png";
  const browser = await chromium.launch({
    executablePath: "/usr/bin/google-chrome",
    headless: true,
    args: ["--no-sandbox"],
  });
  const page = await browser.newPage({
    viewport: { width: 1600, height: 1000 },
    extraHTTPHeaders: { "X-Frappe-Site-Name": "erp-prod.site" },
  });
  await page.goto(`${base}/login`, { waitUntil: "domcontentloaded" });
  await page
    .locator("#login_email")
    .fill(process.env.OCR_E2E_USER || "Administrator");
  await page
    .locator("#login_password")
    .fill(process.env.OCR_E2E_PASSWORD || "1234");
  await page.locator("button.btn-login").click();
  await page.waitForURL((url) => !url.pathname.endsWith("/login"), {
    timeout: 20000,
  });
  await page.goto(`${base}/ocr#run=${encodeURIComponent(run)}`, {
    waitUntil: "networkidle",
  });
  await page.getByRole("tab", { name: "Decision" }).click();
  await page
    .getByText("Human handoff: agent_unavailable")
    .waitFor({ timeout: 20000 });
  await page.screenshot({ path: output, fullPage: true });
  console.log(
    JSON.stringify({
      ok: true,
      title: await page.title(),
      url: page.url(),
      output,
    }),
  );
  await browser.close();
})().catch((error) => {
  console.error(error);
  process.exit(1);
});
