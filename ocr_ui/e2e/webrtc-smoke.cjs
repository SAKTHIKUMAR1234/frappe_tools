const { chromium } = require(
  process.env.PLAYWRIGHT_PACKAGE || "playwright-core",
);
const fs = require("fs");
const path = require("path");

const password = process.env.FRAPPE_TEST_PASSWORD;
if (!password) throw new Error("FRAPPE_TEST_PASSWORD is required");
const baseUrl = process.env.FRAPPE_PROXY_URL || "http://127.0.0.1:8094";
const root = path.resolve(__dirname, "..", "..");
const proofDir = path.join(root, "docs", "proofs");
const samplePath = path.resolve(
  root,
  "..",
  "..",
  "sites",
  "erp-prod.site",
  "private",
  "files",
  "PI-smoke-local.jpg",
);
const sampleBase64 = fs.readFileSync(samplePath).toString("base64");
const executablePath =
  process.env.PLAYWRIGHT_CHROMIUM ||
  "/run/media/sakthi/storage/libraries/playwright-browsers/chromium-1243/chrome-linux64/chrome";
fs.mkdirSync(proofDir, { recursive: true });

(async () => {
  const browser = await chromium.launch({ headless: true, executablePath });
  const browserContext = await browser.newContext({
    viewport: { width: 1440, height: 1000 },
  });
  const phoneContext = await browser.newContext();
  const page = await browserContext.newPage();
  const phone = await phoneContext.newPage();
  try {
    await page.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    await page.locator("#login_email").fill("Administrator");
    await page.locator("#login_password").fill(password);
    await page.locator("button.btn-login:visible").first().click();
    await page.waitForURL((url) => !url.pathname.includes("login"));
    await page.goto(`${baseUrl}/ocr`, { waitUntil: "domcontentloaded" });
    await page
      .getByRole("main")
      .getByRole("button", { name: "Upload document", exact: true })
      .click();
    await page.getByText("Phone scanner", { exact: true }).waitFor();
    const pin = (await page.locator(".live-scanner-pin").innerText()).trim();
    if (!/^\d{4}$/.test(pin)) throw new Error("Browser PIN was not generated");

    await phone.goto(`${baseUrl}/login`, { waitUntil: "domcontentloaded" });
    const transfer = await phone.evaluate(
      async ({ pin, sampleBase64 }) => {
        async function api(method, args = {}) {
          const query = new URLSearchParams();
          for (const [key, value] of Object.entries(args)) {
            query.set(
              key,
              typeof value === "string" ? value : JSON.stringify(value),
            );
          }
          const response = await fetch(`/api/method/${method}?${query}`);
          const payload = await response.json();
          if (!response.ok)
            throw new Error(payload.exception || `HTTP ${response.status}`);
          return payload.message;
        }

        const room = await api("frappe_tools.api.doc_scanner.resolve_pin", {
          pin,
        });
        const iceServers =
          (await api("frappe_tools.api.doc_scanner.get_ice_servers")) || [];
        const peer = new RTCPeerConnection({ iceServers });
        const pendingCandidates = [];
        let channel;
        let acknowledgement;
        const acknowledged = new Promise((resolve) => {
          acknowledgement = resolve;
        });
        peer.onicecandidate = (event) => {
          if (!event.candidate) return;
          void api("frappe_tools.api.doc_scanner.send_signal", {
            room,
            device: "mobile",
            signal_data: {
              type: "candidate",
              candidate: event.candidate.toJSON(),
            },
          });
        };
        peer.ondatachannel = (event) => {
          channel = event.channel;
          channel.onmessage = (message) => {
            const payload = JSON.parse(message.data);
            if (payload.type === "ack") acknowledgement(payload.id);
          };
        };
        await api("frappe_tools.api.doc_scanner.add_scanner", { room });

        const deadline = Date.now() + 30000;
        while (
          Date.now() < deadline &&
          (!channel || channel.readyState !== "open")
        ) {
          const signals =
            (await api("frappe_tools.api.doc_scanner.get_signal", {
              room,
              device: "mobile",
              timeout: 2,
            })) || [];
          for (const signal of signals) {
            if (signal.type === "offer" && signal.sdp) {
              await peer.setRemoteDescription({
                type: "offer",
                sdp: signal.sdp,
              });
              const answer = await peer.createAnswer();
              await peer.setLocalDescription(answer);
              await api("frappe_tools.api.doc_scanner.send_signal", {
                room,
                device: "mobile",
                signal_data: { type: "answer", sdp: answer.sdp },
              });
              for (const candidate of pendingCandidates.splice(0)) {
                await peer.addIceCandidate(candidate);
              }
            } else if (signal.type === "candidate" && signal.candidate) {
              const candidate = new RTCIceCandidate(signal.candidate);
              if (peer.remoteDescription) await peer.addIceCandidate(candidate);
              else pendingCandidates.push(candidate);
            }
          }
        }
        if (!channel || channel.readyState !== "open") {
          throw new Error(
            `WebRTC data channel did not open (${peer.connectionState})`,
          );
        }

        const id = `playwright-${Date.now()}`;
        const envelope = JSON.stringify({ data: sampleBase64 });
        const chunkSize = 16384;
        const total = Math.ceil(envelope.length / chunkSize);
        for (let index = 0; index < total; index += 1) {
          channel.send(
            JSON.stringify({
              type: "chunk",
              id,
              index,
              total,
              data: envelope.slice(index * chunkSize, (index + 1) * chunkSize),
            }),
          );
        }
        const ackId = await Promise.race([
          acknowledged,
          new Promise((_, reject) =>
            setTimeout(
              () => reject(new Error("Browser acknowledgement timed out")),
              15000,
            ),
          ),
        ]);
        const result = {
          roomResolved: Boolean(room),
          dataChannel: channel.readyState,
          chunks: total,
          acknowledged: ackId === id,
          authorizationStoredOnPhone: false,
        };
        channel.close();
        await peer.close();
        return result;
      },
      { pin, sampleBase64 },
    );

    await page.getByText("1 of 20 pages", { exact: true }).waitFor();
    await page.getByText("1 received", { exact: true }).waitFor();
    await page.screenshot({
      path: path.join(proofDir, "webrtc-phone-transfer.png"),
      fullPage: true,
    });
    console.log(
      JSON.stringify({ proxy: baseUrl, ...transfer, pageVisible: true }),
    );
  } finally {
    await phoneContext.close();
    await browserContext.close();
    await browser.close();
  }
})().catch((error) => {
  console.error(error);
  process.exitCode = 1;
});
