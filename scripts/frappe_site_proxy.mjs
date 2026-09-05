import http from "node:http";
import fs from "node:fs";

const listenHost = process.env.FRAPPE_SITE_PROXY_HOST || "127.0.0.1";
const listenPort = Number(process.env.FRAPPE_SITE_PROXY_PORT || 8088);
const upstreamHost = process.env.FRAPPE_SITE_UPSTREAM_HOST || "127.0.0.1";
const upstreamPort = Number(process.env.FRAPPE_SITE_UPSTREAM_PORT || 8000);
const siteName = process.env.FRAPPE_SITE_NAME;
if (!siteName || !/^[a-zA-Z0-9][a-zA-Z0-9.-]*$/.test(siteName)) {
  throw new Error("Set FRAPPE_SITE_NAME to the explicit site hostname.");
}
const downloads = new Map(
  [
    [
      "/downloads/frappe-scanner-arm64.apk",
      process.env.FRAPPE_SCANNER_ARM64_APK,
      "Document-scanner-arm64.apk",
    ],
    [
      "/downloads/frappe-scanner-armv7.apk",
      process.env.FRAPPE_SCANNER_ARMV7_APK,
      "Document-scanner-armv7.apk",
    ],
  ]
    .filter(([, filePath]) => filePath)
    .map(([route, filePath, filename]) => [route, { filePath, filename }]),
);

if (!Number.isInteger(listenPort) || !Number.isInteger(upstreamPort)) {
  throw new Error("Proxy ports must be integers.");
}

function serveDownload(request, response, download) {
  let stats;
  try {
    stats = fs.statSync(download.filePath);
    if (!stats.isFile()) {
      throw new Error("not a file");
    }
  } catch {
    response.writeHead(404, { "content-type": "text/plain; charset=utf-8" });
    response.end("Download not found.\n");
    return;
  }

  let start = 0;
  let end = stats.size - 1;
  let statusCode = 200;
  const range = request.headers.range;

  if (range) {
    const match = /^bytes=(\d*)-(\d*)$/.exec(range);
    if (!match || (!match[1] && !match[2])) {
      response.writeHead(416, { "content-range": `bytes */${stats.size}` });
      response.end();
      return;
    }

    if (!match[1]) {
      const suffixLength = Number(match[2]);
      start = Math.max(stats.size - suffixLength, 0);
    } else {
      start = Number(match[1]);
      end = match[2] ? Number(match[2]) : end;
    }

    if (start >= stats.size || start > end) {
      response.writeHead(416, { "content-range": `bytes */${stats.size}` });
      response.end();
      return;
    }

    end = Math.min(end, stats.size - 1);
    statusCode = 206;
  }

  const headers = {
    "accept-ranges": "bytes",
    "cache-control": "no-store",
    "content-disposition": `attachment; filename="${download.filename}"`,
    "content-length": String(end - start + 1),
    "content-type": "application/vnd.android.package-archive",
  };
  if (statusCode === 206) {
    headers["content-range"] = `bytes ${start}-${end}/${stats.size}`;
  }

  response.writeHead(statusCode, headers);
  if (request.method === "HEAD") {
    response.end();
    return;
  }

  const stream = fs.createReadStream(download.filePath, { start, end });
  stream.on("error", (error) => response.destroy(error));
  stream.pipe(response);
}

const server = http.createServer((request, response) => {
  const requestUrl = new URL(request.url || "/", "http://localhost");
  const download = downloads.get(requestUrl.pathname);
  if (download && (request.method === "GET" || request.method === "HEAD")) {
    serveDownload(request, response, download);
    return;
  }

  const headers = {
    ...request.headers,
    host: siteName,
    "x-frappe-site-name": siteName,
    "x-forwarded-host": request.headers.host || "",
    "x-forwarded-proto": "http",
  };

  const upstream = http.request(
    {
      hostname: upstreamHost,
      port: upstreamPort,
      method: request.method,
      path: request.url,
      headers,
    },
    (upstreamResponse) => {
      response.writeHead(
        upstreamResponse.statusCode || 502,
        upstreamResponse.headers,
      );
      upstreamResponse.pipe(response);
    },
  );

  upstream.setTimeout(5 * 60 * 1000, () => {
    upstream.destroy(new Error("Frappe upstream timed out."));
  });
  upstream.on("error", (error) => {
    if (!response.headersSent) {
      response.writeHead(502, { "content-type": "text/plain; charset=utf-8" });
    }
    response.end(`Frappe site proxy error: ${error.message}\n`);
  });
  request.pipe(upstream);
});

server.requestTimeout = 6 * 60 * 1000;
server.headersTimeout = 65 * 1000;
server.listen(listenPort, listenHost, () => {
  console.log(
    `Frappe site proxy listening on http://${listenHost}:${listenPort} -> http://${upstreamHost}:${upstreamPort} (${siteName})`,
  );
});

function shutdown(signal) {
  console.log(`Frappe site proxy received ${signal}; closing.`);
  server.close(() => process.exit(0));
}

process.on("SIGINT", () => shutdown("SIGINT"));
process.on("SIGTERM", () => shutdown("SIGTERM"));
