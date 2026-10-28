import { defineConfig } from "@playwright/test";

const PORT = Number(process.env.E2E_PORT || 8020);
const PYTHON =
  process.env.E2E_PYTHON || (process.platform === "win32" ? "../backend/.venv/Scripts/python.exe" : "python");

// Uses an installed browser locally (Edge on Windows) so no browser download is needed;
// CI installs Chromium with `npx playwright install chromium` and sets E2E_CHANNEL=chromium.
const channel = process.env.E2E_CHANNEL || (process.platform === "win32" ? "msedge" : "chromium");

export default defineConfig({
  testDir: "e2e",
  timeout: 90_000,
  expect: { timeout: 20_000 },
  fullyParallel: false,
  workers: 1,
  reporter: [["list"]],
  use: {
    baseURL: `http://127.0.0.1:${PORT}`,
    channel: channel === "chromium" ? undefined : channel,
    viewport: { width: 1360, height: 900 },
    trace: "retain-on-failure",
    // Edge's download hub opens UI of its own after a download, which breaks the next test.
    launchOptions: channel === "msedge" ? { args: ["--disable-features=msDownloadsHub,msEdgeDownloadsUI,msPdfOpenInBrowser"] } : {},
  },
  webServer: {
    command: `"${PYTHON}" ../scripts/e2e_server.py ${PORT}`,
    url: `http://127.0.0.1:${PORT}/api/v1/health`,
    reuseExistingServer: false,
    timeout: 120_000,
  },
});
